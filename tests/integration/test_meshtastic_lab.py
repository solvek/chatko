"""The Meshtastic adapter against the docker lab: MeshNode over LibraryMeshApi, driving the lab's
hub node and its radio node, which reach each other only through the lab's Mosquitto (spike S2).

Opt-in: start the lab, then run these tests on their own; the default run deselects them.

    docker compose -f lab/docker-compose.yml up -d
    uv run pytest -m lab tests/integration/test_meshtastic_lab.py

The settings are those of `lab/provision.py`, so the two never undo each other's work. Some tests
stop and restart the lab's containers, and the first one reboots the hub node twice: the whole
module takes a few minutes.
"""

import asyncio
import base64
import hashlib
import logging
import shutil
import socket
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from chatko_meshtastic.api import (
    BROADCAST,
    MeshConnection,
    Packet,
    Text,
    node_id,
)
from chatko_meshtastic.config import MeshtasticConfig
from chatko_meshtastic.library_api import LibraryMeshApi
from chatko_meshtastic.node import Ack, MeshNode, Nak, NodeTimings
from chatko_meshtastic.provisioning import WantedNode

pytestmark = pytest.mark.lab

COMPOSE = Path(__file__).parents[2] / "lab" / "docker-compose.yml"
HOST = "127.0.0.1"
HUB, RADIO = 0xC4A7B001, 0xC4A7B002
PORTS = {"hub": 4403, "radio": 4404}
FAMILY = 1
READY_WITHIN = 90.0
"""Seconds for a node to become ready, including the reboots of provisioning."""


def lab_config(name: str, **changes: Any) -> MeshtasticConfig:
    """The settings `lab/provision.py` gives the node `name`, plus `changes`."""
    short = {"hub": "HUB", "radio": "RAD"}[name]
    raw: dict[str, Any] = {
        "connection": {"tcp": f"{HOST}:{PORTS[name]}"},
        "long_name": {"hub": "chatko hub", "radio": "Lab radio"}[name],
        "short_name": short,
        "region": "EU_868",
        "private_key": b64(hashlib.sha256(b"chatko-lab-key-" + short.encode()).digest()),
        "mqtt": {
            "host": "mosquitto",
            "username": name,
            "password": f"chatko-lab-{name}",
            "root_topic": "msh/lab",
        },
        "channels": [
            {"name": "LongFast", "psk": "AQ=="},
            {"name": "Family", "psk": b64(hashlib.sha256(b"chatko-lab-family").digest())},
        ],
        "min_send_interval_s": 2,
    }
    raw.update(changes)
    return MeshtasticConfig.model_validate(raw)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def compose(*args: str) -> None:
    docker = shutil.which("docker")
    assert docker is not None, "the lab needs docker"
    subprocess.run([docker, "compose", "-f", str(COMPOSE), *args], check=True)  # noqa: S603


class CountingApi(LibraryMeshApi):
    """Counts the connections that MeshNode makes."""

    connections = 0

    async def connect(self) -> MeshConnection:
        connection = await super().connect()
        self.connections += 1
        return connection


class Lab:
    """One node of the lab under a MeshNode, with what it hands over and tells the admin."""

    def __init__(self, config: MeshtasticConfig) -> None:
        self.api = CountingApi(config.connection.host, config.connection.port)
        self.packets: asyncio.Queue[Packet] = asyncio.Queue()
        self.notices: list[str] = []
        self.readies = 0
        self.node = MeshNode(
            self.api,
            WantedNode.from_config(config),
            name=config.short_name,
            logger=logging.getLogger(f"tests.lab.{config.short_name}"),
            notify_admin=self._notify,
            on_packet=self._packet,
            on_ready=self._ready,
            timings=NodeTimings(min_send_interval=config.min_send_interval_s),
        )

    async def _notify(self, text: str, key: str) -> None:
        self.notices.append(text)

    async def _packet(self, packet: Packet) -> None:
        await self.packets.put(packet)

    async def _ready(self) -> None:
        self.readies += 1

    async def start(self) -> None:
        await self.node.start()
        await self.wait_until(lambda: self.node.ready, within=READY_WITHIN)

    async def wait_until(self, check: Callable[[], bool], within: float) -> None:
        async with asyncio.timeout(within):
            while not check():  # noqa: ASYNC110 - the node's state shows only in it
                await asyncio.sleep(0.1)

    async def text(self, matches: Callable[[Packet], bool], within: float = 15.0) -> Packet:
        async with asyncio.timeout(within):
            while True:
                packet = await self.packets.get()
                if isinstance(packet.payload, Text) and matches(packet):
                    return packet


def lab_is_up() -> None:
    for name, port in PORTS.items():
        try:
            socket.create_connection((HOST, port), timeout=2).close()
        except OSError:
            pytest.fail(
                f"the lab's {name} node is not up on {HOST}:{port}; start the lab with "
                f"`docker compose -f {COMPOSE.relative_to(Path.cwd())} up -d`"
            )


@pytest_asyncio.fixture
async def radio() -> AsyncIterator[Lab]:
    lab_is_up()
    lab = Lab(lab_config("radio"))
    await lab.start()
    yield lab
    await lab.node.stop()


@pytest_asyncio.fixture
async def hub(radio: Lab) -> AsyncIterator[Lab]:
    """The hub's node, with the radio's key as a contact."""
    key = radio.node.public_key
    assert key
    lab = Lab(lab_config("hub", contacts={node_id(RADIO): b64(key)}))
    await lab.start()
    yield lab
    await lab.node.stop()


async def test_provisions_the_node_and_serves_it_after_the_reboot() -> None:
    lab_is_up()
    renamed = Lab(lab_config("hub", long_name="chatko lab test"))
    try:
        await renamed.start()
    finally:
        await renamed.node.stop()
    restored = Lab(lab_config("hub"))
    try:
        await restored.start()
    finally:
        await restored.node.stop()
    connection = await restored.api.connect()  # what the node now says of itself
    await connection.close()

    assert renamed.api.connections == 2  # the commit rebooted the node
    assert restored.api.connections == 3  # and once more, and the look at the end
    assert connection.state.settings.long_name == "chatko hub"
    assert not connection.state.settings.ignore_mqtt
    assert renamed.notices == restored.notices == []


async def test_a_provisioned_node_is_served_without_a_reboot(hub: Lab) -> None:
    assert hub.api.connections == 1
    assert hub.node.node(RADIO) is not None
    assert hub.node.node(RADIO).favorite  # type: ignore[union-attr]


async def test_a_channel_text_crosses_both_ways(hub: Lab, radio: Lab) -> None:
    sent = await hub.node.send_text("lab: hub to radio, канал", channel=FAMILY)
    got = await radio.text(lambda packet: packet.packet_id == sent.packet_id)

    assert await sent.outcome(within=10) == Ack(HUB)  # the broker echoed it
    assert (got.sender, got.to, got.channel) == (HUB, BROADCAST, FAMILY)
    assert got.payload == Text("lab: hub to radio, канал")
    assert got.via_mqtt

    back = await radio.node.send_text("lab: radio to hub", channel=FAMILY)
    got = await hub.text(lambda packet: packet.packet_id == back.packet_id)
    assert (got.sender, got.channel, got.is_direct) == (RADIO, FAMILY, False)


async def test_a_direct_message_is_acked_by_the_radio(hub: Lab, radio: Lab) -> None:
    sent = await hub.node.send_text("lab: a direct message", to=RADIO)

    assert await sent.outcome(within=30) == Ack(RADIO)
    assert sent.reached_broker
    got = await radio.text(lambda packet: packet.packet_id == sent.packet_id)
    assert got.is_direct
    assert got.pki
    assert got.public_key == hub.node.public_key


async def test_a_direct_message_from_the_radio_carries_its_key(hub: Lab, radio: Lab) -> None:
    sent = await radio.node.send_text("lab: from the radio", to=HUB)

    got = await hub.text(lambda packet: packet.packet_id == sent.packet_id)
    assert (got.sender, got.to, got.pki) == (RADIO, HUB, True)
    assert got.public_key == radio.node.public_key
    assert await sent.outcome(within=30) == Ack(HUB)


async def test_a_direct_message_to_a_node_without_a_key_is_refused_at_once(hub: Lab) -> None:
    sent = await hub.node.send_text("lab: to nobody", to=0x0BADC0DE)

    assert await sent.outcome(within=10) == Nak(HUB, "PKI_SEND_FAIL_PUBLIC_KEY")


async def test_a_direct_message_to_a_radio_that_is_away_is_given_up(hub: Lab, radio: Lab) -> None:
    await radio.node.stop()
    compose("stop", "radio")
    try:
        sent = await hub.node.send_text("lab: to a radio that is away", to=RADIO)
        outcome = await sent.outcome(within=40)
    finally:
        compose("start", "radio")

    assert outcome == Nak(HUB, "MAX_RETRANSMIT")
    assert sent.reached_broker


async def test_serves_the_node_again_after_it_restarts(hub: Lab) -> None:
    compose("restart", "hub")

    await hub.wait_until(lambda: hub.readies == 2, within=READY_WITHIN)
    assert hub.api.connections == 2  # it needed no provisioning
    sent = await hub.node.send_text("lab: after the restart", channel=FAMILY)
    assert await sent.outcome(within=10) == Ack(HUB)
