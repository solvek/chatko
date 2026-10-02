"""The Meshtastic extension: the channels of the hub's node, and lists of nodes reached by
direct message, as endpoints (design.md §6)."""

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    Other,
    Packet,
    RejectedError,
    Text,
    node_id,
)
from chatko_meshtastic.config import MeshtasticConfig, MeshtasticEndpoint
from chatko_meshtastic.library_api import LibraryMeshApi
from chatko_meshtastic.node import Ack, MeshNode, Nak, NodeTimings
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
    ACK; the firmware gives up after about 23 s, spike S2); `ready` how long a delivery waits
    for a node that is connecting or provisioning; `heard` how often one node is told to the hub
    as heard at one place."""

    node: NodeTimings = field(default_factory=NodeTimings)
    echo: float = 25.0
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


class MeshtasticExtension(Extension[MeshtasticConfig], EndpointProvider[MeshtasticEndpoint]):
    """Reads and posts on the channels of its endpoints through the hub's node (`MeshNode`).

    A `channel` endpoint is one broadcast per message part on that channel of the node, and any
    text on it from another node comes in, with the node's names from its NodeInfo as the
    author (design.md §6.3, §6.4). Every packet from another node tells the hub that the node was
    heard, at the channel endpoint it came on, if any. `dm` endpoints list their nodes as
    recipients; their direct messages come in roadmap S21, and until then a delivery to them
    fails and a direct message to the hub is logged and dropped.

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
        self._heard: dict[tuple[int, EndpointRef | None], datetime] = {}
        """When each node was last told to the hub as heard, at each place."""
        self._sent_parts: OrderedDict[tuple[EndpointRef, MessageId], int] = OrderedDict()

    # Endpoints.

    def set_endpoints(self, endpoints: Mapping[EndpointRef, MeshtasticEndpoint]) -> None:
        places: dict[EndpointRef, _Place] = {}
        by_channel: dict[int, EndpointRef] = {}
        for endpoint, config in endpoints.items():
            if config.channel is None:
                places[endpoint] = _Direct(config.dm or ())
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
        await self._node.start()
        self.logger.info("connecting to the node at %s", api.address)

    async def stop(self) -> None:
        if self._node is not None:
            await self._node.stop()
            self._node = None

    async def _notify_admin(self, text: str, key: str) -> None:
        await self.hub.notify_admin(text, key=key)

    async def _on_ready(self) -> None:
        for endpoint in self._by_channel.values():
            await self.hub.retry_now(endpoint)

    # Reading.

    async def _on_packet(self, packet: Packet) -> None:
        node = self._node
        if node is None or packet.sender == node.num:
            return  # stopped, or the hub's own packet, which MeshNode drops already
        author = self._account(node, packet.sender)
        endpoint = self._endpoint_of(packet)
        await self._tell_heard(packet.sender, author, endpoint)
        payload = packet.payload
        if not isinstance(payload, Text) or payload.reaction or not payload.text.strip():
            return
        if packet.is_direct:
            self.logger.info("dropped a direct message from %s", node_id(packet.sender))
            return
        if endpoint is None:
            return  # a channel that is not an endpoint
        transport_id = f"{node_id(packet.sender)}/{packet.packet_id:08x}"
        await self.hub.submit(InboundMessage(endpoint, transport_id, author, payload.text))

    def _account(self, node: MeshNode, num: int) -> Account:
        """The node as the hub's node knows it: its names from NodeInfo, none before (design.md
        §6.4)."""
        key = AccountKey(self.type_name, node_id(num))
        entry = node.node(num)
        if entry is None:
            return Account(key)
        return Account(key, entry.long_name, entry.short_name or None)

    def _endpoint_of(self, packet: Packet) -> EndpointRef | None:
        """The channel endpoint a packet came on. The channel of a packet the node could not
        decrypt is a hash, not an index (spike S2)."""
        if packet.to != BROADCAST or packet.payload == Other(""):
            return None
        return self._by_channel.get(packet.channel)

    async def _tell_heard(self, num: int, author: Account, endpoint: EndpointRef | None) -> None:
        now = self.hub.now()
        last = self._heard.get((num, endpoint))
        if last is not None and 0 <= (now - last).total_seconds() < self._timings.heard:
            return
        self._heard[(num, endpoint)] = now
        await self.hub.heard(author, endpoint)

    # Delivering.

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        place = self._places.get(endpoint)
        if place is None:
            return Failed(f"{endpoint} is not an endpoint of {self.instance}")
        if isinstance(place, _Direct):
            return self._direct(endpoint, place, message)
        if message.recipient is not None:
            return Failed(f"{endpoint} has no recipients, so not {message.recipient!r}")
        node = self._node
        if node is None:
            return Retry(f"{self.instance} is not running")
        if not await node.wait_ready(self._timings.ready):
            return Retry(f"the node of {self.instance} is not ready")
        return await self._broadcast(node, endpoint, place.index, message)

    def _direct(self, endpoint: EndpointRef, place: _Direct, message: OutboundMessage) -> Failed:
        if message.recipient is None:
            return Failed(f"{endpoint} delivers to each of its nodes, and none was named")
        if message.recipient not in place.nodes:
            return Failed(f"{message.recipient!r} is not a node of {endpoint}")
        return Failed("direct messages are not relayed yet")

    async def _broadcast(
        self, node: MeshNode, endpoint: EndpointRef, channel: int, message: OutboundMessage
    ) -> DeliveryResult:
        """Each part as one packet on the channel, each once the broker echoed the one before;
        a retry goes on from the first part that did not get through."""
        rendered = render(message.author_label, message.plain_text)
        key = (endpoint, message.message_id)
        count = len(rendered.parts)
        for number in range(self._sent_parts.get(key, 0), count):
            try:
                sent = await node.send_text(rendered.parts[number], channel=channel)
            except RejectedError as error:
                self._sent_parts.pop(key, None)
                return Failed(error.reason)
            except MeshError as error:
                return Retry(error.reason)
            outcome = await sent.outcome(within=self._timings.echo)
            if not isinstance(outcome, Ack):
                what = "the message" if count == 1 else f"part {number + 1} of {count}"
                if isinstance(outcome, Nak):
                    return Retry(f"the node gave up on {what}: {outcome.reason}")
                return Retry(f"the broker did not echo {what}")
            self._sent_parts[key] = number + 1
            self._sent_parts.move_to_end(key)
            while len(self._sent_parts) > PARTS_IN_PROGRESS:
                self._sent_parts.popitem(last=False)
        self._sent_parts.pop(key, None)
        return Delivered(truncated=rendered.truncated)
