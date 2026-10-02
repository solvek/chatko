"""The config model of a Meshtastic instance: the hub's node."""

import base64
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from meshtastic.protobuf import config_pb2
from pydantic import ValidationError

from chatko.infrastructure.config import parse_config
from chatko_meshtastic.config import REGIONS, MeshtasticConfig, MeshtasticEndpoint, parse_node_id

ROOT = Path(__file__).parents[3]
KEY = base64.b64encode(bytes(range(32))).decode()
PSK16 = base64.b64encode(bytes(16)).decode()


def section(**changes: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "connection": {"tcp": "meshtasticd:4403"},
        "long_name": "chatko",
        "short_name": "CHKO",
        "region": "EU_433",
        "mqtt": {"host": "mosquitto", "root_topic": "msh/EU_433"},
        "channels": [{"name": "LongFast", "psk": "AQ=="}, {"name": "family", "psk": KEY}],
    }
    config.update(changes)
    return config


def problems(raw: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        MeshtasticConfig.model_validate(raw)
    return [error["msg"] for error in caught.value.errors(include_input=False)]


def test_the_example_section_is_valid() -> None:
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    names = ("TELEGRAM_BOT_TOKEN", "MESH_MQTT_USER", "MESH_MQTT_PASSWORD", "BRIAR_AUTH_TOKEN")
    env = dict.fromkeys(names, "x")
    env |= {"KYIV_PRIMARY_PSK": KEY, "MESH_FAMILY_PSK": PSK16, "MESH_KYIV_PRIVATE_KEY": KEY}
    raw = dict(parse_config(text, env)["extensions"]["kyiv"])
    del raw["type"]

    config = MeshtasticConfig.model_validate(raw)

    assert (config.connection.host, config.connection.port) == ("meshtasticd-kyiv", 4403)
    assert config.channel_index("family") == 1
    assert config.min_send_interval_s == 4


def test_reads_the_node_and_its_settings() -> None:
    config = MeshtasticConfig.model_validate(
        section(private_key=KEY, contacts={"!A1B2C3D4": KEY}, min_send_interval_s=2.5)
    )

    assert config.private_key_bytes == bytes(range(32))
    assert config.contact_keys == {0xA1B2C3D4: bytes(range(32))}
    assert config.channels[0].psk_bytes == b"\x01"
    assert config.channel_index("LongFast") == 0
    assert config.channel_index("street") is None
    assert config.connection.port == 4403


def test_the_port_of_the_node_is_4403_unless_given() -> None:
    config = MeshtasticConfig.model_validate(section(connection={"tcp": "192.168.1.50"}))

    assert (config.connection.host, config.connection.port) == ("192.168.1.50", 4403)


@pytest.mark.parametrize(
    ("mqtt", "address"),
    [
        ({"host": "mosquitto"}, "mosquitto"),
        ({"host": "mosquitto", "port": 1883}, "mosquitto"),
        ({"host": "mosquitto", "port": 1884}, "mosquitto:1884"),
        ({"host": "broker", "tls": True}, "broker"),
        ({"host": "broker", "tls": True, "port": 8883}, "broker"),
        ({"host": "broker", "tls": True, "port": 1883}, "broker:1883"),
    ],
)
def test_writes_the_broker_with_its_port_only_when_it_is_not_the_default(
    mqtt: dict[str, Any], address: str
) -> None:
    config = MeshtasticConfig.model_validate(section(mqtt={**mqtt, "root_topic": "msh"}))

    assert config.mqtt.address == address


def test_hides_the_secrets_when_shown() -> None:
    config = MeshtasticConfig.model_validate(
        section(
            private_key=KEY,
            mqtt={"host": "m", "root_topic": "msh", "password": "hunter2"},
            channels=[{"name": "family", "psk": PSK16}],
        )
    )

    shown = repr(config)
    assert KEY not in shown
    assert PSK16 not in shown
    assert "hunter2" not in shown


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"region": "EU_869"}, "not a Meshtastic region code"),
        ({"region": "UNSET"}, "not a Meshtastic region code"),
        ({"connection": {"tcp": "node:99999"}}, "not host:port"),
        ({"connection": {"tcp": "no de"}}, "not host:port"),
        ({"connection": {"serial": "/dev/ttyUSB0"}}, "Field required"),
        ({"long_name": "x" * 40}, "at most 39 bytes"),
        ({"short_name": "ЧАТК"}, "at most 4 bytes"),
        ({"short_name": " "}, "must not be blank"),
        ({"private_key": PSK16}, "the private key is 16 bytes, not 32"),
        ({"private_key": "not base64!"}, "the private key is not base64"),
        ({"channels": []}, "at least 1 item"),
        ({"channels": [{"name": "a", "psk": PSK16}] * 2}, "listed twice"),
        ({"channels": [{"name": "my channel", "psk": PSK16}]}, "should match pattern"),
        ({"channels": [{"name": "a", "psk": base64.b64encode(bytes(8)).decode()}]}, "8 bytes"),
        ({"contacts": {"a1b2c3d4": KEY}}, "not a node id"),
        ({"contacts": {"!a1b2c3d4": PSK16}}, "the public key of !a1b2c3d4 is 16 bytes"),
        ({"contacts": {"!a1b2c3d4": KEY, "!A1B2C3D4": KEY}}, "!A1B2C3D4 is listed twice"),
        ({"min_send_interval_s": 2}, "greater than or equal to 2.5"),
        ({"mqtt": {"host": "m", "root_topic": "msh/#"}}, "should match pattern"),
        ({"mqtt": {"host": "m:1883", "root_topic": "msh"}}, "should match pattern"),
        ({"mqtt": {"host": "m", "root_topic": "msh", "port": 0}}, "greater than or equal to 1"),
        ({"mqtt": {"host": "m"}}, "Field required"),
        ({"bot_token": "x"}, "Extra inputs are not permitted"),
    ],
)
def test_refuses_settings_that_cannot_work(changes: dict[str, Any], message: str) -> None:
    found = problems(section(**changes))

    assert any(message in problem for problem in found), found


def test_never_shows_a_refused_secret() -> None:
    secret = base64.b64encode(b"secret!!").decode()

    found = problems(section(private_key=secret))

    assert all(secret not in problem for problem in found)


def test_knows_every_region_of_the_library() -> None:
    names = set(config_pb2.Config.LoRaConfig.RegionCode.keys()) - {"UNSET"}

    assert set(REGIONS) == names


@pytest.mark.parametrize(("text", "num"), [("!a1b2c3d4", 0xA1B2C3D4), ("!C4A7B001", 0xC4A7B001)])
def test_reads_node_ids(text: str, num: int) -> None:
    assert parse_node_id(text) == num


@pytest.mark.parametrize("text", ["a1b2c3d4", "!a1b2c3", "!a1b2c3d4e", "!g1b2c3d4"])
def test_refuses_what_is_not_a_node_id(text: str) -> None:
    with pytest.raises(ValueError, match="not a node id"):
        parse_node_id(text)


def test_the_example_lists_the_settings_this_model_takes() -> None:
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    keys = set(raw["extensions"]["kyiv"]) - {"type"}

    assert keys <= set(MeshtasticConfig.model_fields)


# Endpoints.


def test_an_endpoint_is_a_channel_by_name() -> None:
    endpoint = MeshtasticEndpoint.model_validate({"channel": "family"})

    assert (endpoint.channel, endpoint.dm) == ("family", None)


def test_an_endpoint_is_a_list_of_nodes_in_meshtastic_form() -> None:
    endpoint = MeshtasticEndpoint.model_validate({"dm": ["!A1B2C3D4", "!0badc0de"]})

    assert (endpoint.channel, endpoint.dm) == (None, ("!a1b2c3d4", "!0badc0de"))


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ({}, "either a `channel` or a `dm`"),
        ({"channel": "family", "dm": ["!a1b2c3d4"]}, "either a `channel` or a `dm`"),
        ({"dm": []}, "lists no nodes"),
        ({"dm": ["!a1b2c3d4", "!A1B2C3D4"]}, "!a1b2c3d4 is listed twice"),
        ({"dm": ["a1b2c3d4"]}, "not a node id"),
        ({"channel": ""}, "at least 1 character"),
        ({"chat": 1}, "Extra inputs are not permitted"),
    ],
)
def test_refuses_what_is_not_an_endpoint(raw: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(problem)):
        MeshtasticEndpoint.model_validate(raw)
