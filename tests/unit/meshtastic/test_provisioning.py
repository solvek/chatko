"""What the hub writes to its node: only what differs from the config, in a transaction."""

import base64
from dataclasses import replace
from typing import Any

from chatko_meshtastic.api import (
    AddContact,
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
)
from chatko_meshtastic.config import MeshtasticConfig
from chatko_meshtastic.provisioning import (
    WantedNode,
    contact_commands,
    describe,
    describe_all,
    favorite_commands,
    settings_commands,
)
from chatko_meshtastic.testing import fresh_settings

PRIVATE = bytes(range(32))
FAMILY_PSK = bytes(range(32, 64))
ADA_KEY = bytes(range(64, 96))
ADA = 0xA1B2C3D4
OWN = 0xC4A7B001
PASSWORD = "secret"


def config(**changes: Any) -> MeshtasticConfig:
    raw: dict[str, Any] = {
        "connection": {"tcp": "meshtasticd:4403"},
        "long_name": "chatko",
        "short_name": "CHKO",
        "region": "EU_868",
        "private_key": base64.b64encode(PRIVATE).decode(),
        "mqtt": {
            "host": "mosquitto",
            "username": "hub",
            "password": PASSWORD,
            "root_topic": "msh/lab",
        },
        "channels": [
            {"name": "LongFast", "psk": "AQ=="},
            {"name": "Family", "psk": base64.b64encode(FAMILY_PSK).decode()},
        ],
        "contacts": {"!a1b2c3d4": base64.b64encode(ADA_KEY).decode()},
    }
    raw.update(changes)
    return MeshtasticConfig.model_validate(raw)


WANTED = WantedNode.from_config(config())


def provisioned() -> NodeSettings:
    """A node with the settings of `config()`."""
    return NodeSettings(
        long_name="chatko",
        short_name="CHKO",
        region="EU_868",
        ok_to_mqtt=True,
        ignore_mqtt=False,
        private_key=PRIVATE,
        public_key=b"derived",
        mqtt=MqttSettings(
            enabled=True,
            address="mosquitto",
            username="hub",
            password=PASSWORD,
            root="msh/lab",
            encryption=True,
        ),
        channels=(
            ChannelSettings(0, ChannelRole.PRIMARY, "LongFast", b"\x01", True, True),
            ChannelSettings(1, ChannelRole.SECONDARY, "Family", FAMILY_PSK, True, True),
            *(ChannelSettings(index) for index in range(2, 8)),
        ),
    )


def test_the_wanted_node_comes_from_the_config() -> None:
    assert WANTED.mqtt == provisioned().mqtt
    assert WANTED.channels == provisioned().channels[:2]
    assert WANTED.private_key == PRIVATE
    assert WANTED.contacts == {ADA: ADA_KEY}
    assert WANTED.channel(5) == ChannelSettings(5)


def test_a_node_that_matches_needs_nothing() -> None:
    assert settings_commands(provisioned(), WANTED) == []


def test_a_fresh_node_gets_everything_in_one_transaction_in_order() -> None:
    commands = settings_commands(fresh_settings(OWN), WANTED)

    lora = SetLora("EU_868", ok_to_mqtt=True, ignore_mqtt=False)
    assert commands == [
        BeginEdit(),
        SetOwner("chatko", "CHKO"),
        lora,
        lora,  # the firmware turns ignore_mqtt on with the first region; the second turns it off
        SetPrivateKey(PRIVATE),
        SetMqtt(WANTED.mqtt),
        SetChannel(WANTED.channels[0]),
        SetChannel(WANTED.channels[1]),
        CommitEdit(),
    ]


def test_writes_only_what_differs() -> None:
    node = replace(provisioned(), short_name="OLD", ignore_mqtt=True)

    assert settings_commands(node, WANTED) == [
        BeginEdit(),
        SetOwner("chatko", "CHKO"),
        SetLora("EU_868", ok_to_mqtt=True, ignore_mqtt=False),
        CommitEdit(),
    ]


def test_turns_on_ok_to_mqtt_once_without_a_region_change() -> None:
    node = replace(provisioned(), ok_to_mqtt=False)

    assert settings_commands(node, WANTED)[1:-1] == [
        SetLora("EU_868", ok_to_mqtt=True, ignore_mqtt=False)
    ]


def test_leaves_the_key_pair_to_the_node_without_a_private_key_in_the_config() -> None:
    wanted = WantedNode.from_config(config(private_key=None))
    node = replace(provisioned(), private_key=b"the node's own")

    assert settings_commands(node, wanted) == []


def test_replaces_the_mqtt_login() -> None:
    node = replace(provisioned(), mqtt=replace(provisioned().mqtt, password="old"))

    assert settings_commands(node, WANTED)[1:-1] == [SetMqtt(WANTED.mqtt)]


def test_turns_off_channels_that_the_config_does_not_have() -> None:
    channels = list(provisioned().channels)
    channels[3] = ChannelSettings(3, ChannelRole.SECONDARY, "Old", FAMILY_PSK, True, True)
    node = replace(provisioned(), channels=tuple(channels))

    assert settings_commands(node, WANTED)[1:-1] == [SetChannel(ChannelSettings(3))]


def test_leaves_a_disabled_slot_alone_whatever_it_still_holds() -> None:
    channels = list(provisioned().channels)
    channels[4] = ChannelSettings(4, ChannelRole.DISABLED, "Gone", FAMILY_PSK)
    node = replace(provisioned(), channels=tuple(channels))

    assert settings_commands(node, WANTED) == []


def test_rewrites_a_channel_whose_psk_or_flags_differ() -> None:
    channels = list(provisioned().channels)
    channels[1] = replace(channels[1], downlink=False)
    node = replace(provisioned(), channels=tuple(channels[:-1]))  # and a slot the node lacks

    assert settings_commands(node, WANTED)[1:-1] == [SetChannel(WANTED.channels[1])]


def test_adds_a_contact_the_node_does_not_have() -> None:
    assert contact_commands({}, WANTED, own=OWN) == [AddContact(ADA, ADA_KEY)]


def test_adds_a_contact_again_with_the_names_the_node_has() -> None:
    nodes = {ADA: NodeEntry(ADA, "Ada Lovelace", "ADA", b"old key", favorite=True)}

    assert contact_commands(nodes, WANTED, own=OWN) == [
        AddContact(ADA, ADA_KEY, "Ada Lovelace", "ADA")
    ]


def test_makes_a_known_key_a_favorite() -> None:
    nodes = {ADA: NodeEntry(ADA, "Ada", "ADA", ADA_KEY, favorite=False)}

    assert contact_commands(nodes, WANTED, own=OWN) == [AddContact(ADA, ADA_KEY, "Ada", "ADA")]


def test_leaves_a_favorite_with_the_same_key_alone() -> None:
    nodes = {ADA: NodeEntry(ADA, "Ada", "ADA", ADA_KEY, favorite=True)}

    assert contact_commands(nodes, WANTED, own=OWN) == []


def test_never_adds_the_node_itself() -> None:
    assert contact_commands({}, WANTED, own=ADA) == []


def test_makes_the_wanted_nodes_with_a_learned_key_favorites() -> None:
    bob, carol, dave = 0x0BADC0DE, 0x00C0FFEE, 0x0000DAFE
    nodes = {
        ADA: NodeEntry(ADA, "Ada", "ADA", ADA_KEY),
        bob: NodeEntry(bob, "Bob", "BOB", b"bob key", favorite=True),
        carol: NodeEntry(carol, "Carol", "CAR"),  # no key yet
        dave: NodeEntry(dave, "Dave", "DAV", b"dave key"),  # not wanted
    }

    commands = favorite_commands(nodes, {ADA, bob, carol, 0x00000001, OWN}, own=OWN)

    assert commands == [AddContact(ADA, ADA_KEY, "Ada", "ADA")]


def test_never_makes_the_node_itself_a_favorite() -> None:
    nodes = {OWN: NodeEntry(OWN, "chatko", "CHKO", b"own key")}

    assert favorite_commands(nodes, {OWN}, own=OWN) == []


def test_describes_commands_without_secrets() -> None:
    commands = settings_commands(fresh_settings(OWN), WANTED)

    text = describe_all(commands)

    assert text == (
        "names 'chatko'/'CHKO'; region EU_868, OK to MQTT, Ignore MQTT off; private key; "
        "MQTT mosquitto as hub, root msh/lab; channel 0 'LongFast'; channel 1 'Family'"
    )
    assert PASSWORD not in text
    assert describe(BeginEdit()) == "begin settings"
    assert describe(CommitEdit()) == "commit settings"
    assert describe(SetChannel(ChannelSettings(3))) == "channel 3 off"
    assert describe(AddContact(ADA, ADA_KEY)) == "contact !a1b2c3d4"
    tls = SetMqtt(MqttSettings(address="broker", tls=True, root="msh"))
    assert describe(tls) == "MQTT broker with TLS, root msh"
