"""The config of a Meshtastic instance: the hub's node (config.example.yaml, design.md §6.1)."""

import base64
import binascii
import re
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

DEFAULT_TCP_PORT = 4403
"""The port of a node's TCP API (`meshtasticd`)."""

MAX_CHANNELS = 8
MIN_SEND_INTERVAL_S = 2.5
"""The node drops a text from the hub that it handles less than 2 s after the previous one, and
it may handle a text a few hundred milliseconds after the hub sent it: in the lab, texts sent
2.0 to 2.1 s apart were dropped up to every other one, and none at 2.25 s or more (D50)."""

REGIONS = (
    "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU", "IN", "NZ_865", "TH",
    "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923", "PH_433", "PH_868", "PH_915",
    "ANZ_433", "KZ_433", "KZ_863", "NP_865", "BR_902", "ITU1_2M", "ITU2_2M", "EU_866", "EU_874",
    "EU_917", "EU_N_868", "ITU3_2M",
)  # fmt: skip
"""The LoRa region codes of the firmware (`Config.LoRaConfig.RegionCode`), without `UNSET`."""

_NODE_ID = re.compile(r"![0-9a-fA-F]{8}")
_HOST_PORT = re.compile(r"(?P<host>[^\s:]+)(?::(?P<port>[0-9]{1,5}))?")


def parse_node_id(text: str) -> int:
    """The node number of a node id as Meshtastic writes it: `!a1b2c3d4`."""
    if not _NODE_ID.fullmatch(text):
        raise ValueError(f"{text!r} is not a node id like !a1b2c3d4")
    return int(text[1:], 16)


def _base64(text: str, *, sizes: tuple[int, ...], what: str) -> bytes:
    try:
        value = base64.b64decode(text, validate=True)
    except binascii.Error:
        raise ValueError(f"{what} is not base64") from None
    if len(value) not in sizes:
        allowed = " or ".join(str(size) for size in sizes)
        raise ValueError(f"{what} is {len(value)} bytes, not {allowed}")
    return value


def _utf8_at_most(limit: int) -> AfterValidator:
    def check(text: str) -> str:
        if not text.strip():
            raise ValueError("must not be blank")
        if len(text.encode()) > limit:
            raise ValueError(f"must be at most {limit} bytes of UTF-8")
        return text

    return AfterValidator(check)


class TcpConnection(BaseModel):
    """The node's TCP API: `host:port`, the port 4403 by default. A `meshtasticd` container is
    reached inside the Docker network. Serial and BLE connections to a physical node come later
    (design.md §6.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tcp: str

    @field_validator("tcp")
    @classmethod
    def _is_host_port(cls, tcp: str) -> str:
        _split_host_port(tcp)
        return tcp

    @property
    def host(self) -> str:
        return _split_host_port(self.tcp)[0]

    @property
    def port(self) -> int:
        return _split_host_port(self.tcp)[1]


def _split_host_port(text: str) -> tuple[str, int]:
    match = _HOST_PORT.fullmatch(text)
    if match is None or not 0 < int(match["port"] or DEFAULT_TCP_PORT) < 65536:
        raise ValueError("not host:port")
    return match["host"], int(match["port"] or DEFAULT_TCP_PORT)


class MqttConfig(BaseModel):
    """The node's MQTT client: the broker, its login and the root topic (design.md §6.2). The port
    is 1883, or 8883 with TLS, unless given."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: Annotated[str, Field(min_length=1, pattern=r"^[^\s:/]+$")]
    port: Annotated[int | None, Field(ge=1, le=65535, strict=True)] = None
    tls: bool = False
    username: str = ""
    password: SecretStr = SecretStr("")
    root_topic: Annotated[str, Field(min_length=1, pattern=r"^[^#+/\s]+(/[^#+/\s]+)*$")]

    @property
    def address(self) -> str:
        """The broker as the node's settings write it: the host, with the port unless it is the
        default one for the TLS setting."""
        default = 8883 if self.tls else 1883
        if self.port is None or self.port == default:
            return self.host
        return f"{self.host}:{self.port}"


class ChannelConfig(BaseModel):
    """A channel of the node: its name, which MQTT topics carry (design.md §6.1), and its PSK in
    base64: 16 or 32 bytes, or 1 byte for one of the firmware's well-known keys (`AQ==`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, _utf8_at_most(11), Field(pattern=r"^[^\s/#+]+$")]
    psk: SecretStr

    @field_validator("psk")
    @classmethod
    def _is_a_psk(cls, psk: SecretStr) -> SecretStr:
        _base64(psk.get_secret_value(), sizes=(1, 16, 32), what="the PSK")
        return psk

    @property
    def psk_bytes(self) -> bytes:
        return base64.b64decode(self.psk.get_secret_value())


class MeshtasticConfig(BaseModel):
    """One instance: the hub's node, which the hub provisions with these settings (D25, D26).

    `private_key` (base64, 32 bytes) keeps the node's identity when its volume is lost; without
    it the node keeps the key pair it made. `contacts` gives members' nodes and their public keys
    (base64, 32 bytes), which the node stores as favorites. `channels` are the node's channels in
    order: the first is its primary channel.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    connection: TcpConnection
    long_name: Annotated[str, _utf8_at_most(39)]
    short_name: Annotated[str, _utf8_at_most(4)]
    region: str
    private_key: SecretStr | None = None
    mqtt: MqttConfig
    channels: Annotated[list[ChannelConfig], Field(min_length=1, max_length=MAX_CHANNELS)]
    contacts: dict[str, str] = Field(default_factory=dict)
    min_send_interval_s: Annotated[float, Field(ge=MIN_SEND_INTERVAL_S)] = 4.0

    @field_validator("region")
    @classmethod
    def _is_a_region(cls, region: str) -> str:
        if region not in REGIONS:
            raise ValueError(f"not a Meshtastic region code; one of {', '.join(REGIONS)}")
        return region

    @field_validator("private_key")
    @classmethod
    def _is_a_private_key(cls, key: SecretStr | None) -> SecretStr | None:
        if key is not None:
            _base64(key.get_secret_value(), sizes=(32,), what="the private key")
        return key

    @field_validator("contacts")
    @classmethod
    def _are_contacts(cls, contacts: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for node, key in contacts.items():
            number = parse_node_id(node)
            _base64(key, sizes=(32,), what=f"the public key of {node}")
            name = f"!{number:08x}"
            if name in normalized:
                raise ValueError(f"{node} is listed twice")
            normalized[name] = key
        return normalized

    @model_validator(mode="after")
    def _channel_names_are_unique(self) -> Self:
        seen: set[str] = set()
        for channel in self.channels:
            if channel.name in seen:
                raise ValueError(f"the channel {channel.name!r} is listed twice")
            seen.add(channel.name)
        return self

    @property
    def private_key_bytes(self) -> bytes | None:
        if self.private_key is None:
            return None
        return base64.b64decode(self.private_key.get_secret_value())

    @property
    def contact_keys(self) -> dict[int, bytes]:
        """The public key of each contact, by node number."""
        return {parse_node_id(node): base64.b64decode(key) for node, key in self.contacts.items()}

    def channel_index(self, name: str) -> int | None:
        """The index of the channel with this name on the node, if the config has it."""
        for index, channel in enumerate(self.channels):
            if channel.name == name:
                return index
        return None


class MeshtasticEndpoint(BaseModel):
    """An endpoint of the hub's node (design.md §6.2): a `channel` of the node by name, whose
    broadcasts it reads and posts, or a `dm` list of nodes, each a recipient of direct messages.
    Node ids are kept in the form Meshtastic writes them, lower case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: Annotated[str, Field(min_length=1)] | None = None
    dm: tuple[str, ...] | None = None

    @field_validator("dm")
    @classmethod
    def _are_nodes(cls, dm: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if dm is None:
            return None
        if not dm:
            raise ValueError("lists no nodes")
        nodes = tuple(f"!{parse_node_id(node):08x}" for node in dm)
        for node in nodes:
            if nodes.count(node) > 1:
                raise ValueError(f"{node} is listed twice")
        return nodes

    @model_validator(mode="after")
    def _is_one_kind(self) -> Self:
        if (self.channel is None) == (self.dm is None):
            raise ValueError("an endpoint is either a `channel` or a `dm` list of nodes")
        return self
