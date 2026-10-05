"""What the lab tests share: the settings `lab/provision.py` gives the lab's nodes, and a node of
the lab under a `MeshNode` (the lab's hub node in the adapter's tests, its radio node in all).

The lab is the docker compose setup of `lab/README.md`: Mosquitto and two `meshtasticd` nodes,
`hub` and `radio`, which reach each other only through the broker (spike S2).
"""

import asyncio
import base64
import hashlib
import logging
import shutil
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from chatko_meshtastic.api import MeshConnection, Packet, Text, node_id
from chatko_meshtastic.config import MeshtasticConfig
from chatko_meshtastic.library_api import LibraryMeshApi
from chatko_meshtastic.node import MeshNode, NodeTimings
from chatko_meshtastic.provisioning import WantedNode

COMPOSE = Path(__file__).parents[2] / "lab" / "docker-compose.yml"
HOST = "127.0.0.1"
HUB, RADIO = 0xC4A7B001, 0xC4A7B002
PORTS = {"hub": 4403, "radio": 4404}
FAMILY = 1
READY_WITHIN = 90.0
"""Seconds for a node to become ready, including the reboots of provisioning."""
PUBLIC_KEYS = {
    HUB: "QbJtLyEoo3vXigE9UmC/S7Ai9s0ZkW/xdmyH+2yuim8=",
    RADIO: "rwNcZyJw4Y01MFNSJyzpduhjNc5dRS4qDS4QRhNlQRM=",
}
"""The public keys the nodes make from their lab private keys (`lab_key`)."""


def lab_settings(name: str, **changes: Any) -> dict[str, Any]:
    """The settings `lab/provision.py` gives the node `name`, plus `changes`, as in a config.
    The radio has the hub's key as a contact, as after `provision.py`; the hub's contacts are
    the test's."""
    short = {"hub": "HUB", "radio": "RAD"}[name]
    raw: dict[str, Any] = {
        "connection": {"tcp": f"{HOST}:{PORTS[name]}"},
        "long_name": {"hub": "chatko hub", "radio": "Lab radio"}[name],
        "short_name": short,
        "region": "EU_433",
        "private_key": lab_key(short),
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
        "min_send_interval_s": 2.5,
    }
    if name == "radio":
        raw["contacts"] = {node_id(HUB): PUBLIC_KEYS[HUB]}
    raw.update(changes)
    return raw


def lab_config(name: str, **changes: Any) -> MeshtasticConfig:
    return MeshtasticConfig.model_validate(lab_settings(name, **changes))


def lab_key(seed: str) -> str:
    """A private key made from `seed`: `HUB` and `RAD` are the lab nodes' keys."""
    return b64(hashlib.sha256(b"chatko-lab-key-" + seed.encode()).digest())


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def compose(*args: str) -> None:
    docker = shutil.which("docker")
    assert docker is not None, "the lab needs docker"
    subprocess.run([docker, "compose", "-f", str(COMPOSE), *args], check=True)  # noqa: S603


def lab_is_up() -> None:
    for name, port in PORTS.items():
        try:
            socket.create_connection((HOST, port), timeout=2).close()
        except OSError:
            pytest.fail(
                f"the lab's {name} node is not up on {HOST}:{port}; start the lab with "
                f"`docker compose -f {COMPOSE.relative_to(Path.cwd())} up -d`"
            )


async def wait_until(check: Callable[[], bool], within: float) -> None:
    async with asyncio.timeout(within):
        while not check():  # noqa: ASYNC110 - the state shows only in the objects under test
            await asyncio.sleep(0.1)


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
        await wait_until(check, within)

    async def text(self, matches: Callable[[Packet], bool], within: float = 15.0) -> Packet:
        async with asyncio.timeout(within):
            while True:
                packet = await self.packets.get()
                if isinstance(packet.payload, Text) and matches(packet):
                    return packet
