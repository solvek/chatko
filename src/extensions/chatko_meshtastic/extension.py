"""The Meshtastic extension: the channels of the hub's node, and lists of nodes reached by
direct message, as endpoints (design.md §6)."""

import base64
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel

from chatko.extension_api import (
    Account,
    AccountKey,
    Delivered,
    DeliveryResult,
    EndpointProvider,
    EndpointRef,
    Extension,
    Failed,
    HubContext,
    InboundMessage,
    MessageId,
    OutboundMessage,
    Retry,
)
from chatko_meshtastic.api import (
    BROADCAST,
    MeshApi,
    MeshError,
    NodeInfo,
    Other,
    Packet,
    RejectedError,
    Text,
    node_id,
)
from chatko_meshtastic.config import MeshtasticConfig, MeshtasticEndpoint, parse_node_id
from chatko_meshtastic.library_api import LibraryMeshApi
from chatko_meshtastic.node import Ack, MeshNode, Nak, NodeTimings, Outcome, Outgoing
from chatko_meshtastic.provisioning import WantedNode
from chatko_meshtastic.text import render

if TYPE_CHECKING:
    from datetime import datetime

PARTS_IN_PROGRESS: Final = 1024
"""Deliveries whose first parts went out while a later one did not, remembered so that a retry
goes on from there."""


@dataclass(frozen=True, slots=True)
class MeshtasticTimings:
    """Seconds. `echo` is how long a channel packet waits for the broker's echo (the implicit
    ACK; the firmware gives up after about 23 s, spike S2); `ack` how long a direct message
    waits for the destination's ACK or the node's NAK (the node gives up with `MAX_RETRANSMIT`
    after about 23 s); `ready` how long a delivery waits for a node that is connecting or
    provisioning; `heard` how often one node is told to the hub as heard at one place."""

    node: NodeTimings = field(default_factory=NodeTimings)
    echo: float = 25.0
    ack: float = 30.0
    ready: float = 10.0
    heard: float = 60.0

    @classmethod
    def from_config(cls, config: MeshtasticConfig) -> "MeshtasticTimings":
        return cls(NodeTimings(min_send_interval=config.min_send_interval_s))


@dataclass(frozen=True, slots=True)
class _Channel:
    index: int


@dataclass(frozen=True, slots=True)
class _Direct:
    nodes: tuple[str, ...]


type _Place = _Channel | _Direct


class _Wait(Enum):
    """What the deliveries to one node of a `dm` endpoint wait for (D26): the hub asks for them
    at once when it comes, instead of waiting for the outbox's backoff."""

    HEARD = "heard"
    """The node did not acknowledge: it is away, until it is heard again."""
    KEY = "key"
    """The hub's node has no public key of the node, until it learns one."""


class MeshtasticExtension(Extension[MeshtasticConfig], EndpointProvider[MeshtasticEndpoint]):
    """Reads and posts on the channels and to the nodes of its endpoints through the hub's node
    (`MeshNode`).

    A `channel` endpoint is one broadcast per message part on that channel of the node, and any
    text on it from another node comes in, with the node's names from its NodeInfo as the
    author (design.md §6.3, §6.4). A `dm` endpoint lists nodes, each a recipient: a message goes
    to each as direct messages, delivered only by that node's ACK, and a direct message from one
    of them comes in at the first `dm` endpoint that lists it (design.md §6.2). Every packet
    from another node tells the hub that the node was heard: at the channel endpoint it came
    on, at the `dm` endpoints that list a node that sent it to the hub, or at no endpoint.

    `api` replaces the connection to the node (tests); without it, `start` connects over TCP.
    """

    type_name: ClassVar[str] = "meshtastic"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = MeshtasticConfig
    endpoint_config_model: ClassVar[type[BaseModel]] = MeshtasticEndpoint

    def __init__(
        self,
        instance: str,
        config: MeshtasticConfig,
        hub: HubContext,
        *,
        api: MeshApi | None = None,
        timings: MeshtasticTimings | None = None,
    ) -> None:
        super().__init__(instance, config, hub)
        self._given_api = api
        self._timings = timings or MeshtasticTimings.from_config(config)
        self._node: MeshNode | None = None
        self._places: dict[EndpointRef, _Place] = {}
        self._by_channel: dict[int, EndpointRef] = {}
        self._by_node: dict[int, tuple[EndpointRef, ...]] = {}
        """The `dm` endpoints that list each node, in config order."""
        self._heard: dict[tuple[int, EndpointRef | None], datetime] = {}
        """When each node was last told to the hub as heard, at each place."""
        self._sent_parts: OrderedDict[tuple[EndpointRef, MessageId, str | None], int] = (
            OrderedDict()
        )
        self._waits: dict[int, dict[EndpointRef, _Wait]] = {}
        """What the waiting deliveries to each node wait for, by `dm` endpoint."""
        self._told_keys: set[int] = set()
        """Nodes the admin was told of a key mismatch, until one acknowledges again."""

    # Endpoints.

    def set_endpoints(self, endpoints: Mapping[EndpointRef, MeshtasticEndpoint]) -> None:
        places: dict[EndpointRef, _Place] = {}
        by_channel: dict[int, EndpointRef] = {}
        by_node: dict[int, tuple[EndpointRef, ...]] = {}
        for endpoint, config in endpoints.items():
            if config.channel is None:
                nodes = config.dm or ()
                places[endpoint] = _Direct(nodes)
                for num in map(parse_node_id, nodes):
                    by_node[num] = (*by_node.get(num, ()), endpoint)
                continue
            index = self.config.channel_index(config.channel)
            if index is None:
                names = ", ".join(channel.name for channel in self.config.channels)
                raise ValueError(
                    f"{endpoint}: the node of {self.instance} has no channel {config.channel!r}; "
                    f"its channels are {names}"
                )
            if index in by_channel:
                raise ValueError(f"{endpoint} and {by_channel[index]} are the same channel")
            by_channel[index] = endpoint
            places[endpoint] = _Channel(index)
        self._places = places
        self._by_channel = by_channel
        self._by_node = by_node
        if self._node is not None:
            self._node.keep_favorites(by_node)

    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]:
        place = self._places.get(endpoint)
        return place.nodes if isinstance(place, _Direct) else ()

    # Lifecycle.

    @property
    def ready(self) -> bool:
        """Whether the hub's node is connected and provisioned."""
        return self._node is not None and self._node.ready

    async def start(self) -> None:
        connection = self.config.connection
        api = self._given_api or LibraryMeshApi(connection.host, connection.port)
        self._node = MeshNode(
            api,
            WantedNode.from_config(self.config),
            name=self.instance,
            logger=self.logger,
            notify_admin=self._notify_admin,
            on_packet=self._on_packet,
            on_ready=self._on_ready,
            timings=self._timings.node,
        )
        self._node.keep_favorites(self._by_node)
        await self._node.start()
        self.logger.info("connecting to the node at %s", api.address)

    async def stop(self) -> None:
        if self._node is not None:
            await self._node.stop()
            self._node = None

    async def _notify_admin(self, text: str, key: str) -> None:
        await self.hub.notify_admin(text, key=key)

    async def _on_ready(self) -> None:
        for endpoint in self._places:
            await self.hub.retry_now(endpoint)

    # Reading.

    async def _on_packet(self, packet: Packet) -> None:
        node = self._node
        if node is None or packet.sender == node.num:
            return  # stopped, or the hub's own packet, which MeshNode drops already
        author = self._account(node, packet.sender)
        places = self._places_of(node, packet)
        for place in places:
            await self._tell_heard(packet.sender, author, place)
        await self._wake(node, packet.sender)
        payload = packet.payload
        if isinstance(payload, NodeInfo):
            await self._check_key(node, packet.sender, payload)
        if not isinstance(payload, Text) or payload.reaction or not payload.text.strip():
            return
        transport_id = f"{node_id(packet.sender)}/{packet.packet_id:08x}"
        if not packet.is_direct:
            if places[0] is not None:
                await self.hub.submit(InboundMessage(places[0], transport_id, author, payload.text))
            return
        if packet.to != node.num:
            return
        sender = node_id(packet.sender)
        if not packet.pki:
            self.logger.info("dropped a direct message from %s that is not PKI-encrypted", sender)
        elif places[0] is None:
            self.logger.info("dropped a direct message from %s, a node of no dm endpoint", sender)
        else:
            await self.hub.submit(
                InboundMessage(places[0], transport_id, author, payload.text, from_recipient=sender)
            )

    def _account(self, node: MeshNode, num: int) -> Account:
        """The node as the hub's node knows it: its names from NodeInfo, none before (design.md
        §6.4)."""
        key = AccountKey(self.type_name, node_id(num))
        entry = node.node(num)
        if entry is None:
            return Account(key)
        return Account(key, entry.long_name, entry.short_name or None)

    def _places_of(self, node: MeshNode, packet: Packet) -> tuple[EndpointRef | None, ...]:
        """Where a packet was heard: the channel endpoint it came on, or the `dm` endpoints of
        the node that sent it to the hub (the first one gets its messages), or no endpoint.
        The channel of a packet the node could not decrypt is a hash, not an index (spike S2)."""
        if packet.is_direct:
            return self._by_node.get(packet.sender, (None,)) if packet.to == node.num else (None,)
        if packet.payload == Other(""):
            return (None,)
        return (self._by_channel.get(packet.channel),)

    async def _tell_heard(self, num: int, author: Account, endpoint: EndpointRef | None) -> None:
        now = self.hub.now()
        last = self._heard.get((num, endpoint))
        if last is not None and 0 <= (now - last).total_seconds() < self._timings.heard:
            return
        self._heard[(num, endpoint)] = now
        await self.hub.heard(author, endpoint)

    async def _wake(self, node: MeshNode, num: int) -> None:
        """Ask the hub for the deliveries to a node that waited for it to be heard, or for its
        key once the hub's node has one."""
        waits = self._waits.get(num)
        if not waits:
            return
        entry = node.node(num)
        has_key = entry is not None and bool(entry.public_key)
        for endpoint, wait in list(waits.items()):
            if wait is _Wait.HEARD or has_key:
                del waits[endpoint]
                await self.hub.retry_now(endpoint, node_id(num))
        if not waits:
            del self._waits[num]

    def _wait(self, num: int, endpoint: EndpointRef, wait: _Wait) -> None:
        self._waits.setdefault(num, {})[endpoint] = wait

    async def _check_key(self, node: MeshNode, num: int, info: NodeInfo) -> None:
        """Tell the admin when a node of a `dm` endpoint announces another key than the one
        the hub's node pinned for it: the node drops that NodeInfo (D26)."""
        entry = node.node(num)
        if (
            num not in self._by_node
            or entry is None
            or not entry.public_key
            or not info.public_key
            or info.public_key == entry.public_key
        ):
            return
        who = node_id(num)
        key = base64.b64encode(info.public_key).decode()
        await self._tell_keys(
            num,
            f"The radio {who}{_named(entry.long_name)} announces a new public key, but the "
            f"Meshtastic node of {self.instance} keeps the one it had, so direct messages "
            "between them fail. If the radio was reset, check the key with its owner and put "
            f'it into the contacts of {self.instance}: "{who}": "{key}".',
        )

    async def _tell_keys(self, num: int, text: str) -> None:
        """One key-mismatch notice per node, until the node acknowledges a message again."""
        if num not in self._told_keys:
            self._told_keys.add(num)
            await self.hub.notify_admin(text, key=f"key-mismatch:{node_id(num)}")

    # Delivering.

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        address = self._address(endpoint, message)
        if isinstance(address, Failed):
            return address
        node = self._node
        if node is None:
            return Retry(f"{self.instance} is not running")
        if not await node.wait_ready(self._timings.ready):
            return Retry(f"the node of {self.instance} is not ready")
        to, channel = address
        return await self._post(node, endpoint, message, to=to, channel=channel)

    def _address(self, endpoint: EndpointRef, message: OutboundMessage) -> tuple[int, int] | Failed:
        """Where the packets of a delivery go: `to` (a node, or `BROADCAST`) and the channel."""
        place = self._places.get(endpoint)
        if place is None:
            return Failed(f"{endpoint} is not an endpoint of {self.instance}")
        if isinstance(place, _Channel):
            if message.recipient is not None:
                return Failed(f"{endpoint} has no recipients, so not {message.recipient!r}")
            return BROADCAST, place.index
        if message.recipient is None:
            return Failed(f"{endpoint} delivers to each of its nodes, and none was named")
        if message.recipient not in place.nodes:
            return Failed(f"{message.recipient!r} is not a node of {endpoint}")
        return parse_node_id(message.recipient), 0

    async def _post(
        self,
        node: MeshNode,
        endpoint: EndpointRef,
        message: OutboundMessage,
        *,
        to: int,
        channel: int,
    ) -> DeliveryResult:
        """Each part as one packet, a broadcast on the channel or a direct message to the node
        `to`, each once the one before got through; a retry goes on from the first part that
        did not."""
        rendered = render(message.author_label, message.plain_text)
        key = (endpoint, message.message_id, message.recipient)
        count = len(rendered.parts)
        for number in range(self._sent_parts.get(key, 0), count):
            what = "the message" if count == 1 else f"part {number + 1} of {count}"
            try:
                sent = await node.send_text(rendered.parts[number], to=to, channel=channel)
            except RejectedError as error:
                self._sent_parts.pop(key, None)
                return Failed(error.reason)
            except MeshError as error:
                return Retry(error.reason)
            if to == BROADCAST:
                retry = _echoed(await sent.outcome(within=self._timings.echo), what)
            else:
                outcome = await sent.outcome(within=self._timings.ack)
                retry = await self._acknowledged(node, endpoint, sent, outcome, what)
            if retry is not None:
                return retry
            self._sent_parts[key] = number + 1
            self._sent_parts.move_to_end(key)
            while len(self._sent_parts) > PARTS_IN_PROGRESS:
                self._sent_parts.popitem(last=False)
        self._sent_parts.pop(key, None)
        return Delivered(truncated=rendered.truncated)

    async def _acknowledged(
        self,
        node: MeshNode,
        endpoint: EndpointRef,
        sent: Outgoing,
        outcome: Outcome | None,
        what: str,
    ) -> Retry | None:
        """`None` when the node a direct message went to acknowledged it, else when to try
        again (D26): once the node is heard again, once the hub's node has its key, or after
        the outbox's backoff."""
        num = sent.to
        if isinstance(outcome, Ack):
            self._told_keys.discard(num)
            return None
        if isinstance(outcome, Nak):
            return await self._refused(node, endpoint, num, outcome.reason, what)
        if not sent.reached_broker:
            return Retry(f"the node dropped {what} to {node_id(num)}")
        self._wait(num, endpoint, _Wait.HEARD)
        return Retry(f"{node_id(num)} did not acknowledge {what}")

    async def _refused(
        self, node: MeshNode, endpoint: EndpointRef, num: int, reason: str, what: str
    ) -> Retry:
        """When to try again a direct message that got a NAK, from the hub's node or from the
        node it went to."""
        who = node_id(num)
        match reason:
            case "MAX_RETRANSMIT":
                self._wait(num, endpoint, _Wait.HEARD)
                return Retry(f"{who} did not acknowledge {what} (MAX_RETRANSMIT): it is away")
            case "PKI_SEND_FAIL_PUBLIC_KEY":
                self._wait(num, endpoint, _Wait.KEY)
                return Retry(f"the hub's node has no public key of {who} yet ({reason})")
            case "PKI_UNKNOWN_PUBKEY":
                return Retry(f"{who} did not know the hub's key ({reason}); the node sent it")
            case "NO_CHANNEL":
                await self._tell_no_channel(node, num)
                return Retry(f"{who} cannot decrypt the hub's direct messages ({reason})")
            case _:
                return Retry(f"the node gave up on {what} to {who}: {reason}")

    async def _tell_no_channel(self, node: MeshNode, num: int) -> None:
        entry = node.node(num)
        who = node_id(num)
        hub_key = base64.b64encode(node.public_key or b"").decode()
        await self._tell_keys(
            num,
            f"The radio {who}{_named(entry.long_name if entry else '')} cannot decrypt the "
            f"direct messages of the Meshtastic node of {self.instance} (NO_CHANNEL): one of "
            "them holds an old key of the other. If the radio was reset, put its new public key "
            f"into the contacts of {self.instance}. If the hub's node has a new key ({hub_key}), "
            "the radio's owner removes the hub from the radio's node list, so that it learns "
            "the new one. Messages to the radio wait until then.",
        )


def _echoed(outcome: Outcome | None, what: str) -> Retry | None:
    """`None` when the broker echoed a broadcast (the implicit ACK), else the retry."""
    if isinstance(outcome, Ack):
        return None
    if isinstance(outcome, Nak):
        return Retry(f"the node gave up on {what}: {outcome.reason}")
    return Retry(f"the broker did not echo {what}")


def _named(name: str) -> str:
    return f" ({name})" if name else ""
