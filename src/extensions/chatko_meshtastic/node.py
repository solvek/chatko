"""`MeshNode`: the hub's Meshtastic node, kept connected and provisioned (design.md §6.1, D25, D26).

It is the only client of its node. It connects through a `MeshApi`, provisions the node from the
config on every connection (writing only what differs, one admin message at a time, each waited
for), and connects again whenever the node closes the connection: after the reboot that every
commit of settings causes, after a crash, or when another client connected. Packets from other
nodes go to `on_packet`; ACKs and NAKs are matched to the texts they answer by their request id.
The nodes the extension names (`keep_favorites`) are made favorites as soon as the node has their
keys.
"""

import asyncio
import base64
import contextlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from typing import Final

from chatko_meshtastic.api import (
    BROADCAST,
    AdminCommand,
    MeshApi,
    MeshConnection,
    MeshError,
    NodeEntry,
    NodeInfo,
    NodeState,
    Packet,
    RejectedError,
    Routing,
    UnreachableError,
    node_id,
)
from chatko_meshtastic.provisioning import (
    WantedNode,
    contact_commands,
    describe,
    describe_all,
    favorite_commands,
    settings_commands,
)

type Notify = Callable[[str, str], Awaitable[None]]
"""Posts an admin notice: the text and the key that rate-limits it (`HubContext.notify_admin`)."""

type PacketHandler = Callable[[Packet], Awaitable[None]]
type ReadyHandler = Callable[[], Awaitable[None]]

UNCLAIMED_ANSWERS: Final = 64
"""Answers kept for requests not registered yet: the node may answer before the hub has the
packet id of what it sent."""


@dataclass(frozen=True, slots=True)
class Ack:
    """A text arrived: at the node `by` it was sent to, or, for a broadcast, at the broker, which
    the hub's own node reports as an implicit ACK (D26)."""

    by: int


@dataclass(frozen=True, slots=True)
class Nak:
    """A text did not arrive: `reason` is the firmware's (`MAX_RETRANSMIT`, `NO_CHANNEL`,
    `PKI_UNKNOWN_PUBKEY`, …), and `by` the node that said so."""

    by: int
    reason: str


type Outcome = Ack | Nak


@dataclass(frozen=True, slots=True)
class NodeTimings:
    """Seconds. `reconnect` is the first and the longest wait between attempts to connect,
    doubling in between."""

    min_send_interval: float = 4.0
    admin_timeout: float = 10.0
    reboot_timeout: float = 30.0
    reconnect: tuple[float, float] = (1.0, 30.0)
    unreachable_notice: float = 120.0
    """How long the node may be unreachable before the admin is told."""
    stable_connection: float = 60.0
    """A connection that the node closes sooner, without a commit, counts as dropped."""
    dropped_notice: int = 5
    """After this many dropped connections in a row, the admin is told."""
    outgoing_ttl: float = 300.0
    """How long a text waits for its ACK at most, whoever waits for it."""


class NotReadyError(UnreachableError):
    """The node is not connected and provisioned now."""


class Outgoing:
    """A text the node took from the hub, waiting for its ACK or NAK (D26).

    For a direct message only the destination's ACK means delivered: the implicit ACK of the
    hub's own node, when the broker echoes the packet back, only sets `reached_broker`. For a
    broadcast the implicit ACK is all there is.
    """

    def __init__(self, packet_id: int, to: int, own: int, created: float) -> None:
        self.packet_id = packet_id
        self.to = to
        self.own = own
        self.created = created
        self.reached_broker = False
        self._outcome: asyncio.Future[Outcome | None] = asyncio.get_running_loop().create_future()

    @property
    def done(self) -> bool:
        return self._outcome.done()

    async def outcome(self, within: float) -> Outcome | None:
        """The ACK or NAK, or `None` if none came within `within` seconds or the connection
        ended first. Without even `reached_broker`, the node dropped the text (e.g. one that came
        too soon, design.md §6.3)."""
        await asyncio.wait({self._outcome}, timeout=within)
        return self._outcome.result() if self._outcome.done() else None

    def answer(self, sender: int, error: str) -> None:
        if self.done:
            return
        if error != "NONE":
            self._outcome.set_result(Nak(sender, error))
        elif sender == self.to:
            self._outcome.set_result(Ack(sender))
        elif sender == self.own:
            self.reached_broker = True
            if self.to == BROADCAST:
                self._outcome.set_result(Ack(sender))

    def end(self) -> None:
        if not self.done:
            self._outcome.set_result(None)


class _NoAnswerError(MeshError):
    pass


class _RefusedError(MeshError):
    pass


_READY: Final = object()


class MeshNode:
    """The hub's node: connected, provisioned and serving texts.

    `start` returns at once and connects in the background; `ready` tells whether the node is
    connected and provisioned, and `send_text` raises `NotReadyError` while it is not. The admin is
    told when the node stays unreachable, when it keeps closing the connection (another client),
    and when it refuses or does not keep a setting or a contact.
    """

    def __init__(
        self,
        api: MeshApi,
        wanted: WantedNode,
        *,
        name: str,
        logger: logging.Logger,
        notify_admin: Notify,
        on_packet: PacketHandler | None = None,
        on_ready: ReadyHandler | None = None,
        timings: NodeTimings | None = None,
    ) -> None:
        self._api = api
        self._wanted = wanted
        self._name = name
        self._log = logger
        self._notify_admin = notify_admin
        self._on_packet = on_packet
        self._on_ready = on_ready
        self._timings = timings or NodeTimings()
        self._runner: asyncio.Task[None] | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._inbox: asyncio.Queue[Packet | object] = asyncio.Queue()
        self._state: NodeState | None = None
        self._ready: MeshConnection | None = None
        self._ready_event = asyncio.Event()
        self._nodes: dict[int, NodeEntry] = {}
        self._waiting: dict[int, asyncio.Future[Packet]] = {}
        self._outgoing: dict[int, Outgoing] = {}
        self._unclaimed: OrderedDict[int, list[Packet]] = OrderedDict()
        self._send_lock = asyncio.Lock()
        self._last_text: float | None = None
        self._committed: str | None = None
        """What the last commit wrote, until the next connection has checked that it stuck."""
        self._logged_key: bytes | None = None
        self._favorites: frozenset[int] = frozenset()
        self._favorites_due = asyncio.Event()

    # What the extension sees.

    @property
    def ready(self) -> bool:
        return self._ready is not None

    async def wait_ready(self, within: float) -> bool:
        """Whether the node is ready, or becomes ready within `within` seconds."""
        if not self.ready:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(within):
                    await self._ready_event.wait()
        return self.ready

    @property
    def num(self) -> int | None:
        """The node's number, once it has been connected."""
        return None if self._state is None else self._state.num

    @property
    def public_key(self) -> bytes | None:
        """The node's public key, once it has been connected."""
        return None if self._state is None else self._state.settings.public_key

    def node(self, num: int) -> NodeEntry | None:
        """Another node as the hub's node has it: from its database when it was last connected,
        the contacts the hub added, and the NodeInfo it took since (not the ones it dropped
        because they carry another key than the one it pinned, D26)."""
        return self._nodes.get(num)

    def keep_favorites(self, nums: Iterable[int]) -> None:
        """Keep these nodes as favorites of the node, besides the contacts of the config: each
        as soon as the node has its key, from its database or from a NodeInfo it takes. A
        favorite is saved at once and never evicted from the node database (D26). Safe to call
        at any time; the set replaces the one before."""
        self._favorites = frozenset(nums)
        self._favorites_due.set()

    async def start(self) -> None:
        self._dispatcher = asyncio.create_task(self._dispatch(), name=f"chatko.mesh.{self._name}")
        self._runner = asyncio.create_task(self._run(), name=f"chatko.mesh.{self._name}.node")

    async def stop(self) -> None:
        for task in (self._runner, self._dispatcher):
            if task is not None:
                task.cancel()
                await asyncio.wait({task})
        self._runner = self._dispatcher = None

    async def send_text(
        self, text: str, *, to: int = BROADCAST, channel: int = 0, want_ack: bool = True
    ) -> Outgoing:
        """Hand the node a text for a channel (`to` is `BROADCAST`) or a node, at least
        `min_send_interval` after the previous one: the node drops a text that comes sooner
        than 2 s (spike S2). Raises `NotReadyError` while the node is not ready, and the port's
        `MeshError`s."""
        loop = asyncio.get_running_loop()
        async with self._send_lock:
            if self._last_text is not None:
                wait = self._last_text + self._timings.min_send_interval - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
            connection, state = self._ready, self._state
            if connection is None or state is None:
                raise NotReadyError(f"the node at {self._api.address} is not ready")
            packet_id = await connection.send_text(text, to=to, channel=channel, want_ack=want_ack)
            self._last_text = loop.time()
        outgoing = Outgoing(packet_id, to, state.num, loop.time())
        self._track(outgoing)
        return outgoing

    # Connecting.

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        failures = dropped = 0
        down_since: float | None = None
        told_down = False
        while True:
            try:
                connection = await self._api.connect()
            except Exception as error:
                if isinstance(error, MeshError):
                    reason = error.reason
                    self._log.warning("cannot reach the node at %s: %s", self._api.address, reason)
                else:
                    reason = f"{type(error).__name__}: {error}"
                    self._log.exception("cannot connect to the node at %s", self._api.address)
                failures += 1
                now = loop.time()
                down_since = now if down_since is None else down_since
                if not told_down and now - down_since >= self._timings.unreachable_notice:
                    told_down = True
                    await self._notify(
                        f"The Meshtastic node of {self._name} at {self._api.address} has been "
                        f"unreachable for {round(now - down_since)} s: {reason}. The hub keeps "
                        "trying.",
                        key="node-unreachable",
                    )
                await asyncio.sleep(self._backoff(failures))
                continue
            if told_down:
                self._log.info("the node at %s is reachable again", self._api.address)
            failures, down_since, told_down = 0, None, False
            started = loop.time()
            try:
                rebooted = await self._serve(connection)
            except Exception:
                self._log.exception("lost the node at %s", self._api.address)
                rebooted = False
            finally:
                await connection.close()
            if rebooted or loop.time() - started >= self._timings.stable_connection:
                dropped = 0
                continue
            dropped += 1
            if dropped == self._timings.dropped_notice:
                await self._notify(
                    f"The Meshtastic node of {self._name} keeps closing the hub's connection. "
                    "Is the Meshtastic app or CLI connected to it? The hub must be its only "
                    "client.",
                    key="node-dropped",
                )
            await asyncio.sleep(self._backoff(dropped))

    def _backoff(self, failures: int) -> float:
        first, longest = self._timings.reconnect
        return min(first * 2.0 ** min(failures - 1, 32), longest)

    async def _serve(self, connection: MeshConnection) -> bool:
        """Provision the node and serve it until the connection ends. True when it ended
        because the node reboots to save its settings."""
        state = connection.state
        self._state = state
        self._nodes = {entry.num: entry for entry in state.nodes}
        self._unclaimed.clear()
        reader = asyncio.create_task(self._read(connection), name=f"chatko.mesh.{self._name}.read")
        try:
            if await self._provision(connection, state):
                self._log.info("node %s reboots to save its settings", node_id(state.num))
                await asyncio.wait({reader}, timeout=self._timings.reboot_timeout)
                if not reader.done():
                    self._log.warning("node %s did not reboot after a commit", node_id(state.num))
                return True
            self._ready = connection
            self._ready_event.set()
            self._log_ready(state)
            self._inbox.put_nowait(_READY)
            await self._serve_ready(connection, reader)
            self._log.warning("the connection to node %s ended", node_id(state.num))
        except UnreachableError as error:
            self._log.warning("the connection to node %s ended: %s", node_id(state.num), error)
        except _NoAnswerError as error:
            self._log.warning("node %s: %s; connecting again", node_id(state.num), error)
        finally:
            self._ready = None
            self._ready_event.clear()
            reader.cancel()
            await asyncio.wait({reader})
            for outgoing in self._outgoing.values():
                outgoing.end()
            self._outgoing.clear()
        return False

    async def _serve_ready(self, connection: MeshConnection, reader: asyncio.Task[None]) -> None:
        """Keep the favorites until the connection ends. Raises what ended the keeping: an
        admin message without an answer, or the end of the connection."""
        self._favorites_due.set()
        keeper = asyncio.create_task(
            self._keep_favorites(connection), name=f"chatko.mesh.{self._name}.favorites"
        )
        try:
            await asyncio.wait({reader, keeper}, return_when=asyncio.FIRST_COMPLETED)
            if keeper.done():
                keeper.result()
        finally:
            keeper.cancel()
            await asyncio.wait({keeper})

    async def _keep_favorites(self, connection: MeshConnection) -> None:
        own = connection.state.num
        while True:
            await self._favorites_due.wait()
            self._favorites_due.clear()
            for contact in favorite_commands(self._nodes, self._favorites, own=own):
                self._log.info("node %s: keeping %s as a favorite", node_id(own), describe(contact))
                if await self._command(connection, contact):
                    entry = self._nodes.get(contact.num)
                    if entry is not None and entry.public_key == contact.public_key:
                        self._nodes[contact.num] = replace(entry, favorite=True)

    def _log_ready(self, state: NodeState) -> None:
        key = state.settings.public_key
        if key != self._logged_key:
            self._logged_key = key
            self._log.info(
                "node %s (%s) is ready; its public key is %s",
                node_id(state.num),
                state.settings.long_name,
                base64.b64encode(key).decode(),
            )
        else:
            self._log.info("node %s is ready again", node_id(state.num))

    # Provisioning.

    async def _provision(self, connection: MeshConnection, state: NodeState) -> bool:
        """Write what differs from the config, or the contacts once nothing does. True when
        settings were committed, so the node reboots."""
        commands = settings_commands(state.settings, self._wanted)
        committed, self._committed = self._committed, None
        if commands and committed is not None:
            await self._notify(
                f"The Meshtastic node of {self._name} ({node_id(state.num)}) still differs from "
                f"the config after it saved {committed}: {describe_all(commands)}. The hub "
                "goes on with the node as it is.",
                key="provisioning",
            )
        elif commands:
            self._log.info("provisioning node %s: %s", node_id(state.num), describe_all(commands))
            for command in commands:
                await self._command(connection, command)
            self._committed = describe_all(commands)
            return True
        for contact in contact_commands(self._nodes, self._wanted, own=state.num):
            self._log.info("node %s: adding %s", node_id(state.num), describe(contact))
            if await self._command(connection, contact):
                self._nodes[contact.num] = NodeEntry(
                    contact.num,
                    contact.long_name,
                    contact.short_name,
                    contact.public_key,
                    favorite=True,
                )
        return False

    async def _command(self, connection: MeshConnection, command: AdminCommand) -> bool:
        """Send one admin message and wait for its answer. False when the node refused it."""
        try:
            await self._admin(connection, command)
        except (_RefusedError, RejectedError) as refused:
            await self._notify(
                f"The Meshtastic node of {self._name} refused {describe(command)}: "
                f"{refused.reason}.",
                key=f"refused:{describe(command)}",
            )
            return False
        return True

    async def _admin(self, connection: MeshConnection, command: AdminCommand) -> None:
        packet_id = await connection.send_admin(command)
        own = connection.state.num
        future: asyncio.Future[Packet] = asyncio.get_running_loop().create_future()
        self._waiting[packet_id] = future
        for packet in self._unclaimed.pop(packet_id, []):
            if packet.sender == own and not future.done():
                future.set_result(packet)
        try:
            async with asyncio.timeout(self._timings.admin_timeout):
                answer = await future
        except TimeoutError:
            raise _NoAnswerError(f"no answer to {describe(command)}") from None
        finally:
            self._waiting.pop(packet_id, None)
        if isinstance(answer.payload, Routing) and answer.payload.error != "NONE":
            raise _RefusedError(answer.payload.error)

    # Reading.

    async def _read(self, connection: MeshConnection) -> None:
        try:
            async for packet in connection.events():
                try:
                    self._take(packet)
                except Exception:
                    self._log.exception("could not read a packet from the node")
        finally:
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(UnreachableError("the connection ended"))

    def _take(self, packet: Packet) -> None:
        if self._state is None:
            return
        own = self._state.num
        if packet.request_id and not self._answers(packet, own):
            self._keep_unclaimed(packet)
        if packet.sender == own:
            return
        if isinstance(packet.payload, NodeInfo):
            self._learn(packet.sender, packet.payload)
        self._inbox.put_nowait(packet)

    def _answers(self, packet: Packet, own: int) -> bool:
        """Hand an answer to whatever waits for it; False if nothing does yet."""
        future = self._waiting.get(packet.request_id)
        if future is not None and packet.sender == own:
            if not future.done():
                future.set_result(packet)
            return True
        outgoing = self._outgoing.get(packet.request_id)
        if outgoing is not None and isinstance(packet.payload, Routing):
            outgoing.answer(packet.sender, packet.payload.error)
            if outgoing.done:
                del self._outgoing[packet.request_id]
            return True
        return False

    def _keep_unclaimed(self, packet: Packet) -> None:
        self._unclaimed.setdefault(packet.request_id, []).append(packet)
        while len(self._unclaimed) > UNCLAIMED_ANSWERS:
            self._unclaimed.popitem(last=False)

    def _track(self, outgoing: Outgoing) -> None:
        stale = outgoing.created - self._timings.outgoing_ttl
        for packet_id, old in list(self._outgoing.items()):
            if old.created < stale:
                old.end()
                del self._outgoing[packet_id]
        self._outgoing[outgoing.packet_id] = outgoing
        for packet in self._unclaimed.pop(outgoing.packet_id, []):
            self._answers(packet, outgoing.own)

    def _learn(self, num: int, info: NodeInfo) -> None:
        """Take a NodeInfo as the node does (firmware 2.7): a node with a pinned key only takes
        NodeInfo with the same key, which updates the names; a node without one takes anything."""
        entry = self._nodes.get(num)
        if entry is None or not entry.public_key:
            favorite = entry is not None and entry.favorite
            self._nodes[num] = NodeEntry(
                num, info.long_name, info.short_name, info.public_key, favorite=favorite
            )
        elif info.public_key == entry.public_key:
            self._nodes[num] = NodeEntry(
                num, info.long_name, info.short_name, entry.public_key, favorite=entry.favorite
            )
        if num in self._favorites:
            self._favorites_due.set()

    async def _dispatch(self) -> None:
        while True:
            item = await self._inbox.get()
            try:
                if item is _READY:
                    if self._on_ready is not None:
                        await self._on_ready()
                elif isinstance(item, Packet) and self._on_packet is not None:
                    await self._on_packet(item)
            except Exception:
                self._log.exception("could not handle what the node handed over")

    async def _notify(self, text: str, *, key: str) -> None:
        try:
            await self._notify_admin(text, key)
        except Exception:
            self._log.exception("could not post an admin notice")
