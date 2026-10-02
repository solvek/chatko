"""What the hub writes to its node so that the node matches the config (D25, D26).

Pure functions over the port's types: `MeshNode` sends what they return, one admin message at a
time.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from chatko_meshtastic.api import (
    AddContact,
    AdminCommand,
    BeginEdit,
    ChannelRole,
    ChannelSettings,
    CommitEdit,
    MqttSettings,
    NodeEntry,
    NodeSettings,
    SetChannel,
    SetLora,
    SetMqtt,
    SetOwner,
    SetPrivateKey,
    node_id,
)
from chatko_meshtastic.config import MAX_CHANNELS, MeshtasticConfig


@dataclass(frozen=True, slots=True)
class WantedNode:
    """The node as the config wants it. `private_key` is `None` when the node keeps its own."""

    long_name: str
    short_name: str
    region: str
    private_key: bytes | None = field(repr=False)
    mqtt: MqttSettings
    channels: tuple[ChannelSettings, ...]
    contacts: Mapping[int, bytes] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: MeshtasticConfig) -> "WantedNode":
        mqtt = config.mqtt
        return cls(
            long_name=config.long_name,
            short_name=config.short_name,
            region=config.region,
            private_key=config.private_key_bytes,
            mqtt=MqttSettings(
                enabled=True,
                address=mqtt.address,
                username=mqtt.username,
                password=mqtt.password.get_secret_value(),
                root=mqtt.root_topic,
                encryption=True,
                json=False,
                tls=mqtt.tls,
                proxy_to_client=False,
            ),
            channels=tuple(
                ChannelSettings(
                    index=index,
                    role=ChannelRole.PRIMARY if index == 0 else ChannelRole.SECONDARY,
                    name=channel.name,
                    psk=channel.psk_bytes,
                    uplink=True,
                    downlink=True,
                )
                for index, channel in enumerate(config.channels)
            ),
            contacts=config.contact_keys,
        )

    def channel(self, index: int) -> ChannelSettings:
        """The wanted channel slot: a configured channel, else a disabled slot."""
        if index < len(self.channels):
            return self.channels[index]
        return ChannelSettings(index)


def settings_commands(current: NodeSettings, wanted: WantedNode) -> list[AdminCommand]:
    """The admin messages that bring the node's settings to the wanted ones, in the order to
    send them, inside a settings transaction; empty when they match.

    Only what differs is written. The LoRa settings go twice when the region changes: setting a
    region with a duty-cycle limit for the first time makes the firmware turn `ignore_mqtt` on
    whatever the message says, and only a second message turns it off (spike S2). The private
    key follows the region, because a node makes no key pair before it has one.
    """
    commands: list[AdminCommand] = []
    if (current.long_name, current.short_name) != (wanted.long_name, wanted.short_name):
        commands.append(SetOwner(wanted.long_name, wanted.short_name))
    lora = SetLora(wanted.region, ok_to_mqtt=True, ignore_mqtt=False)
    if current.region != wanted.region:
        commands += [lora, lora]
    elif not current.ok_to_mqtt or current.ignore_mqtt:
        commands.append(lora)
    if wanted.private_key is not None and current.private_key != wanted.private_key:
        commands.append(SetPrivateKey(wanted.private_key))
    if current.mqtt != wanted.mqtt:
        commands.append(SetMqtt(wanted.mqtt))
    have = {channel.index: channel for channel in current.channels}
    for index in range(MAX_CHANNELS):
        channel = wanted.channel(index)
        if not _same_channel(have.get(index, ChannelSettings(index)), channel):
            commands.append(SetChannel(channel))
    if not commands:
        return []
    return [BeginEdit(), *commands, CommitEdit()]


def _same_channel(have: ChannelSettings, want: ChannelSettings) -> bool:
    if want.role is ChannelRole.DISABLED:
        return have.role is ChannelRole.DISABLED
    return have == want


def contact_commands(
    nodes: Mapping[int, NodeEntry], wanted: WantedNode, *, own: int
) -> list[AddContact]:
    """`add_contact` for each configured contact whose key the node does not have as a
    favorite. The names the node already has for it are kept."""
    commands: list[AddContact] = []
    for num, key in wanted.contacts.items():
        if num == own:
            continue
        entry = nodes.get(num, NodeEntry(num))
        if entry.public_key != key or not entry.favorite:
            commands.append(AddContact(num, key, entry.long_name, entry.short_name))
    return commands


def describe(command: AdminCommand) -> str:
    """The command for logs and notices, without keys or passwords."""
    match command:
        case BeginEdit():
            text = "begin settings"
        case CommitEdit():
            text = "commit settings"
        case SetOwner():
            text = f"names {command.long_name!r}/{command.short_name!r}"
        case SetLora():
            text = f"region {command.region}, OK to MQTT, Ignore MQTT off"
        case SetPrivateKey():
            text = "private key"
        case SetMqtt():
            mqtt = command.mqtt
            login = f" as {mqtt.username}" if mqtt.username else ""
            tls = " with TLS" if mqtt.tls else ""
            text = f"MQTT {mqtt.address}{tls}{login}, root {mqtt.root}"
        case SetChannel(channel=channel) if channel.role is ChannelRole.DISABLED:
            text = f"channel {channel.index} off"
        case SetChannel(channel=channel):
            text = f"channel {channel.index} {channel.name!r}"
        case AddContact():
            text = f"contact {node_id(command.num)}"
    return text


def describe_all(commands: Sequence[AdminCommand]) -> str:
    """The settings commands, without the transaction around them."""
    return "; ".join(
        dict.fromkeys(
            describe(command)
            for command in commands
            if not isinstance(command, BeginEdit | CommitEdit)
        )
    )
