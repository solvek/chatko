"""The `MeshApi` port: the hub's Meshtastic node as the extension needs it, in its own terms.

`LibraryMeshApi` implements it over the `meshtastic` library, TCP to the node;
`chatko_meshtastic.testing.FakeMeshApi` fakes it for the tests (docs/architecture.md §3.6).
`MeshNode` builds the rest on it: reconnecting, provisioning, matching ACKs.

One `MeshConnection` is one connection to the node, from `MeshApi.connect` until the node closes
it (it reboots, or another client connects) or the hub does. A new connection starts with what
the node tells a client first: its settings and its node database (`NodeState`).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

BROADCAST: Final = 0xFFFFFFFF
"""The `to` of a packet for everyone on its channel."""


def node_id(num: int) -> str:
    """A node number as Meshtastic writes it: `!a1b2c3d4`."""
    return f"!{num:08x}"


# What the node tells a new connection.


class ChannelRole(StrEnum):
    DISABLED = "DISABLED"
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """One of the node's 8 channel slots, by index; the first one is the primary channel."""

    index: int
    role: ChannelRole = ChannelRole.DISABLED
    name: str = ""
    psk: bytes = field(default=b"", repr=False)
    uplink: bool = False
    downlink: bool = False


@dataclass(frozen=True, slots=True)
class MqttSettings:
    """The node's MQTT client. `address` is the broker, `host` or `host:port`."""

    enabled: bool = False
    address: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    root: str = ""
    encryption: bool = False
    json: bool = False
    tls: bool = False
    proxy_to_client: bool = False


@dataclass(frozen=True, slots=True)
class NodeSettings:
    """The settings of the node that the hub provisions (D25). `region` is the name of the
    firmware's region code, `UNSET` on a fresh node, which has no key pair either."""

    long_name: str
    short_name: str
    region: str
    ok_to_mqtt: bool
    ignore_mqtt: bool
    private_key: bytes = field(repr=False)
    public_key: bytes
    mqtt: MqttSettings
    channels: tuple[ChannelSettings, ...]


@dataclass(frozen=True, slots=True)
class NodeEntry:
    """Another node as the node's database has it: its names and the public key it pinned."""

    num: int
    long_name: str = ""
    short_name: str = ""
    public_key: bytes = b""
    favorite: bool = False


@dataclass(frozen=True, slots=True)
class NodeState:
    """What the node tells a new connection: its number, its settings and its database of other
    nodes."""

    num: int
    settings: NodeSettings
    nodes: tuple[NodeEntry, ...] = ()


# What the node hands the connection: packets.


@dataclass(frozen=True, slots=True)
class Text:
    text: str


@dataclass(frozen=True, slots=True)
class Routing:
    """An ACK (`NONE`) or a NAK (`MAX_RETRANSMIT`, `PKI_UNKNOWN_PUBKEY`, …) for the packet of the
    `Packet.request_id`; the answer to an admin message is one too."""

    error: str = "NONE"


@dataclass(frozen=True, slots=True)
class NodeInfo:
    """A node announcing itself. The node keeps the first key it learned for another node and
    drops a NodeInfo with another one, but the connection gets it anyway (spike S2)."""

    long_name: str
    short_name: str
    public_key: bytes = b""


@dataclass(frozen=True, slots=True)
class Other:
    """Any other packet: `port` is its port name, `""` for one the node could not decrypt."""

    port: str


type Payload = Text | Routing | NodeInfo | Other


@dataclass(frozen=True, slots=True)
class Packet:
    """A packet the node handed to the connection. `channel` is the local channel index (0 for
    a direct message); `request_id` is the packet it answers, if any; `public_key` is the
    sender's key of a PKI direct message (`pki`). The node has no clock, so a packet carries no
    time."""

    sender: int
    to: int
    packet_id: int
    payload: Payload
    channel: int = 0
    request_id: int = 0
    want_ack: bool = False
    pki: bool = False
    public_key: bytes = b""
    via_mqtt: bool = False
    hop_start: int = 0
    hop_limit: int = 0

    @property
    def is_direct(self) -> bool:
        """Whether it is addressed to one node rather than to a channel."""
        return self.to != BROADCAST


# What the connection asks of the node: admin messages, each answered on its own.


@dataclass(frozen=True, slots=True)
class BeginEdit:
    """Start a settings transaction: the node saves the settings and reboots at the commit."""


@dataclass(frozen=True, slots=True)
class CommitEdit:
    """Save the settings of the transaction; the node reboots a few seconds later."""


@dataclass(frozen=True, slots=True)
class SetOwner:
    long_name: str
    short_name: str


@dataclass(frozen=True, slots=True)
class SetLora:
    """The LoRa settings the hub provisions; the node's others stay as they are."""

    region: str
    ok_to_mqtt: bool
    ignore_mqtt: bool


@dataclass(frozen=True, slots=True)
class SetPrivateKey:
    """The node's private key; the node derives the public key from it."""

    private_key: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SetMqtt:
    """The MQTT settings the hub provisions; the node's others (map reports) stay as they are."""

    mqtt: MqttSettings


@dataclass(frozen=True, slots=True)
class SetChannel:
    """One channel slot; the node's other settings of the channel stay as they are."""

    channel: ChannelSettings


@dataclass(frozen=True, slots=True)
class AddContact:
    """Store a node with its public key as a favorite: saved at once, never evicted from the
    node database, and replacing a key the node pinned before (D26)."""

    num: int
    public_key: bytes
    long_name: str = ""
    short_name: str = ""


type AdminCommand = (
    BeginEdit | CommitEdit | SetOwner | SetLora | SetPrivateKey | SetMqtt | SetChannel | AddContact
)


# Errors.


class MeshError(Exception):
    """A call to the node failed. `reason` is safe to show the admin: no keys or passwords."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UnreachableError(MeshError):
    """The node cannot be reached, or the connection to it ended. Connect again later."""


class RejectedError(MeshError):
    """The request cannot work, so sending it again cannot help (a text too long)."""


# The port.


class MeshConnection(ABC):
    """One connection to the node. Every method raises only `MeshError`s."""

    @property
    @abstractmethod
    def state(self) -> NodeState:
        """What the node told the connection when it was made."""

    @abstractmethod
    def events(self) -> AsyncIterator[Packet]:
        """The packets the node hands over, in order, until the connection ends. Only one
        consumer reads them."""

    @abstractmethod
    async def send_text(self, text: str, *, to: int, channel: int, want_ack: bool) -> int:
        """Hand the node a text for a channel (`to` is `BROADCAST`) or a node, and return the
        packet id, which the ACKs and NAKs for it carry as their `request_id`."""

    @abstractmethod
    async def send_admin(self, command: AdminCommand) -> int:
        """Hand the node an admin message that asks for an answer, and return its packet id,
        which the answer carries as its `request_id`. Send one at a time: the node drops the
        oldest of more than 4 waiting (D26)."""

    @abstractmethod
    async def close(self) -> None:
        """End the connection; `events` ends too. Safe to call more than once."""


class MeshApi(ABC):
    """Connects to the hub's node."""

    @property
    @abstractmethod
    def address(self) -> str:
        """Where the node is, for logs and notices: `tcp meshtasticd:4403`."""

    @abstractmethod
    async def connect(self) -> MeshConnection:
        """A new connection, once the node has told it its settings and node database. Raises
        `UnreachableError` when the node cannot be reached. The node serves one client at a
        time: a new connection ends the previous one."""
