"""A fake of the `MeshApi` port: the hub's node in memory, for the extension's tests and its
contract driver.

It behaves like `meshtasticd` where the hub relies on it (spike S2): it applies admin messages to
its settings and answers each one, reboots (ends the connection) after a commit, turns
`ignore_mqtt` on when a duty-cycle region is first set, serves one connection at a time, and
answers texts with an implicit ACK and, for a direct message, the destination's ACK.
"""

import asyncio
import hashlib
import itertools
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, replace

from chatko_meshtastic.api import (
    BROADCAST,
    AddContact,
    AdminCommand,
    BeginEdit,
    ChannelRole,
    ChannelSettings,
    CommitEdit,
    MeshApi,
    MeshConnection,
    MqttSettings,
    NodeEntry,
    NodeSettings,
    NodeState,
    Packet,
    Payload,
    Routing,
    SetChannel,
    SetLora,
    SetMqtt,
    SetOwner,
    SetPrivateKey,
    Text,
    UnreachableError,
)

HUB_NUM = 0xC4A7B001
"""The fake node's number by default: the lab's hub node."""

DUTY_CYCLE_REGIONS = frozenset({"EU_433", "EU_868", "UA_433", "UA_868"})
"""Regions for which the firmware turns `ignore_mqtt` on when one is first set."""


def fresh_settings(num: int = HUB_NUM) -> NodeSettings:
    """A node that was never provisioned: no region, no keys, the default primary channel."""
    suffix = f"{num:08x}"[-4:]
    return NodeSettings(
        long_name=f"Meshtastic {suffix}",
        short_name=suffix,
        region="UNSET",
        ok_to_mqtt=False,
        ignore_mqtt=False,
        private_key=b"",
        public_key=b"",
        mqtt=MqttSettings(root="msh", encryption=True),
        channels=(
            ChannelSettings(0, ChannelRole.PRIMARY, "", b"\x01"),
            *(ChannelSettings(index) for index in range(1, 8)),
        ),
    )


def public_key_of(private_key: bytes) -> bytes:
    """The fake's stand-in for the key derivation of the firmware."""
    return hashlib.sha256(b"public:" + private_key).digest()


@dataclass(frozen=True, slots=True)
class SentText:
    packet_id: int
    text: str
    to: int
    channel: int
    want_ack: bool


class FakeConnection(MeshConnection):
    def __init__(self, api: "FakeMeshApi", state: NodeState) -> None:
        self._api = api
        self._state = state
        self._queue: asyncio.Queue[Packet | None] = asyncio.Queue()
        self.ended = False
        self.closed = False

    @property
    def state(self) -> NodeState:
        return self._state

    async def events(self) -> AsyncIterator[Packet]:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.put_nowait(None)  # later readers end too
                return
            yield item

    def push(self, packet: Packet) -> None:
        if not self.ended:
            self._queue.put_nowait(packet)

    def end(self) -> None:
        if not self.ended:
            self.ended = True
            self._queue.put_nowait(None)

    async def send_text(self, text: str, *, to: int, channel: int, want_ack: bool) -> int:
        self._check()
        return await self._api.take_text(
            self, SentText(next(self._api.ids), text, to, channel, want_ack)
        )

    async def send_admin(self, command: AdminCommand) -> int:
        self._check()
        packet_id = self._api.take_admin(self, command, next(self._api.ids))
        if self._api.answer_early:
            for _ in range(10):
                await asyncio.sleep(0)
        return packet_id

    async def close(self) -> None:
        self.closed = True
        self.end()

    def _check(self) -> None:
        if self.ended:
            raise UnreachableError("the connection to the fake node ended")


class FakeMeshApi(MeshApi):
    """The hub's node in memory. A test makes things happen at the node (`receive`, `drop`,
    `online`), reads what the hub did (`admin`, `texts`, `settings`, `nodes`) and decides how
    the node answers (`ack_texts`, `answer_admin`, `refuse`, `ignore`, `answer_early`)."""

    def __init__(
        self,
        *,
        num: int = HUB_NUM,
        settings: NodeSettings | None = None,
        nodes: Iterable[NodeEntry] = (),
    ) -> None:
        self.num = num
        self.settings = settings or fresh_settings(num)
        self.nodes: dict[int, NodeEntry] = {entry.num: entry for entry in nodes}
        self.online = True
        self.ack_texts = True
        """Answer a text that wants an ACK: the implicit ACK, and the destination's for a DM."""
        self.answer_admin = True
        self.answer_early = False
        """Answers reach the connection before the hub has the packet id of what it sent."""
        self.reboot_on_commit = True
        self.refuse: dict[type[AdminCommand], str] = {}
        """Admin messages of these types are answered with this error."""
        self.ignore: set[type[AdminCommand]] = set()
        """Admin messages of these types are answered, but change nothing."""
        self.connections = 0
        self.admin: list[AdminCommand] = []
        self.texts: list[SentText] = []
        self.max_waiting_admin = 0
        """The most admin messages that waited for their answers at once."""
        self.ids = itertools.count(1)
        self._waiting_admin = 0
        self._connection: FakeConnection | None = None

    @property
    def address(self) -> str:
        return "fake node"

    async def connect(self) -> MeshConnection:
        if not self.online:
            raise UnreachableError("the fake node is offline")
        if self._connection is not None:
            self._connection.end()  # a node serves one client at a time
        self.connections += 1
        state = NodeState(self.num, self.settings, tuple(self.nodes.values()))
        self._connection = FakeConnection(self, state)
        return self._connection

    # What happens at the node.

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.ended

    def receive(self, packet: Packet) -> None:
        """The node hands the connection a packet."""
        if self._connection is not None:
            self._connection.push(packet)

    def answer(self, request_id: int, *, by: int, error: str = "NONE") -> None:
        """A node answers a packet with an ACK, or with a NAK for an `error`."""
        self.receive(_routing(by, self.num, request_id, error))

    def drop(self) -> None:
        """The node ends the connection: it reboots, or another client connected."""
        if self._connection is not None:
            self._connection.end()

    def settings_commands(self) -> list[AdminCommand]:
        """The admin messages that were not contacts."""
        return [command for command in self.admin if not isinstance(command, AddContact)]

    # The node at work.

    async def take_text(self, connection: FakeConnection, sent: SentText) -> int:
        self.texts.append(sent)
        if self.ack_texts and sent.want_ack:
            answers = [_routing(self.num, self.num, sent.packet_id)]
            if sent.to != BROADCAST:
                answers.append(_routing(sent.to, self.num, sent.packet_id))
            self._answer(connection, answers)
            if self.answer_early:
                for _ in range(10):
                    await asyncio.sleep(0)
        return sent.packet_id

    def take_admin(self, connection: FakeConnection, command: AdminCommand, packet_id: int) -> int:
        self.admin.append(command)
        error = self.refuse.get(type(command))
        if error is None and type(command) not in self.ignore:
            self._apply(command)
        loop = asyncio.get_running_loop()
        if self.answer_admin:
            self._waiting_admin += 1
            self.max_waiting_admin = max(self.max_waiting_admin, self._waiting_admin)
            answer = _routing(self.num, self.num, packet_id, error or "NONE")
            if self.answer_early:
                self._answered(connection, answer)
            else:
                loop.call_soon(self._answered, connection, answer)
        if isinstance(command, CommitEdit) and self.reboot_on_commit:
            loop.call_soon(connection.end)  # after the answer, as the node reboots a bit later
        return packet_id

    def _answered(self, connection: FakeConnection, answer: Packet) -> None:
        self._waiting_admin -= 1
        connection.push(answer)

    def _answer(self, connection: FakeConnection, answers: list[Packet]) -> None:
        if self.answer_early:
            for answer in answers:
                connection.push(answer)
        else:
            loop = asyncio.get_running_loop()
            for answer in answers:
                loop.call_soon(connection.push, answer)

    def _apply(self, command: AdminCommand) -> None:
        settings = self.settings
        match command:
            case SetOwner():
                settings = replace(
                    settings, long_name=command.long_name, short_name=command.short_name
                )
            case SetLora():
                first = settings.region == "UNSET" and command.region in DUTY_CYCLE_REGIONS
                settings = replace(
                    settings,
                    region=command.region,
                    ok_to_mqtt=command.ok_to_mqtt,
                    ignore_mqtt=True if first else command.ignore_mqtt,
                )
            case SetPrivateKey():
                settings = replace(
                    settings,
                    private_key=command.private_key,
                    public_key=public_key_of(command.private_key),
                )
            case SetMqtt():
                settings = replace(settings, mqtt=command.mqtt)
            case SetChannel():
                channels = list(settings.channels)
                channels[command.channel.index] = command.channel
                settings = replace(settings, channels=tuple(channels))
            case AddContact():
                self.nodes[command.num] = NodeEntry(
                    command.num,
                    command.long_name,
                    command.short_name,
                    command.public_key,
                    favorite=True,
                )
            case BeginEdit() | CommitEdit():
                pass
        self.settings = settings


def _routing(sender: int, to: int, request_id: int, error: str = "NONE") -> Packet:
    payload: Payload = Routing(error)
    return Packet(sender, to, 0, payload, request_id=request_id)


def text_packet(
    sender: int, text: str, *, packet_id: int, to: int = BROADCAST, channel: int = 0
) -> Packet:
    """A text from another node, as the node hands it over: on a channel, or a PKI direct
    message to `to`."""
    direct = to != BROADCAST
    return Packet(
        sender,
        to,
        packet_id,
        Text(text),
        channel=0 if direct else channel,
        pki=direct,
        via_mqtt=True,
    )
