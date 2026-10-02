"""The contract suite against the Meshtastic extension's `channel` and `dm` endpoints, over the
fake node."""

import asyncio
import contextlib
from typing import Any

from pydantic import BaseModel

from chatko.extension_api import Extension, HubContext
from chatko.extension_api.testing import ContractDriver, ExtensionContract
from chatko_meshtastic import MeshtasticConfig, MeshtasticExtension, MeshtasticTimings
from chatko_meshtastic.api import BROADCAST, NodeInfo, Packet, node_id
from chatko_meshtastic.node import NodeTimings
from chatko_meshtastic.testing import HUB_NUM, FakeMeshApi, text_packet

ADA = 0xA1B2C3D4
PLACES = 3
TIMINGS = MeshtasticTimings(
    NodeTimings(
        min_send_interval=0.0,
        admin_timeout=0.2,
        reboot_timeout=0.3,
        reconnect=(0.005, 0.02),
    ),
    echo=0.5,
    ready=0.3,
)


def channel(place: int) -> str:
    return f"place{place}"


def nodes(place: int) -> tuple[int, ...]:
    """The nodes of the `dm` endpoint at a place: two at each, none shared."""
    return (0xA1B20000 + 2 * place, 0xA1B20001 + 2 * place)


class MeshtasticDriver(ContractDriver[Packet]):
    def __init__(self) -> None:
        self.api = FakeMeshApi()
        self.extension: MeshtasticExtension | None = None
        self.ids = iter(range(1000, 2000))

    @property
    def extension_class(self) -> type[Extension[Any]]:
        return MeshtasticExtension

    def config(self) -> dict[str, Any]:
        return {
            "connection": {"tcp": "meshtasticd:4403"},
            "long_name": "chatko",
            "short_name": "CHKO",
            "region": "EU_868",
            "mqtt": {"host": "mosquitto", "root_topic": "msh/contract"},
            "channels": [
                {"name": "LongFast", "psk": "AQ=="},
                *({"name": channel(place), "psk": "AQ=="} for place in range(PLACES)),
            ],
        }

    def endpoint_config(self, place: int) -> dict[str, Any]:
        return {"channel": channel(place)}

    def create(self, instance: str, config: BaseModel, hub: HubContext) -> Extension[Any]:
        assert isinstance(config, MeshtasticConfig)
        self.extension = MeshtasticExtension(instance, config, hub, api=self.api, timings=TIMINGS)
        return self.extension

    async def receive(
        self, place: int, text: str, *, by_hub: bool = False, recipient: str | None = None
    ) -> Packet:
        await self._ready()
        sender = HUB_NUM if by_hub else ADA
        self.api.receive(Packet(ADA, BROADCAST, next(self.ids), NodeInfo("Ada Lovelace", "ADA")))
        post = text_packet(sender, text, packet_id=next(self.ids), channel=place + 1)
        self.api.receive(post)
        return post

    async def receive_again(self, post: Packet) -> None:
        self.api.receive(post)

    def posted(self, place: int) -> list[str]:
        return [sent.text for sent in self.api.texts if sent.channel == place + 1]

    def go_offline(self) -> None:
        self.api.online = False
        self.api.drop()

    async def settle(self) -> None:
        await super().settle()
        await asyncio.sleep(0.05)

    async def _ready(self) -> None:
        """Wait until the node is provisioned, unless the extension stopped."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(0.5):
                while self.extension is None or not self.extension.ready:  # noqa: ASYNC110
                    await asyncio.sleep(0.005)


class MeshtasticDmDriver(MeshtasticDriver):
    """Places are `dm` endpoints, each a list of nodes that send the hub direct messages."""

    def endpoint_config(self, place: int) -> dict[str, Any]:
        return {"dm": [node_id(num) for num in nodes(place)]}

    async def receive(
        self, place: int, text: str, *, by_hub: bool = False, recipient: str | None = None
    ) -> Packet:
        await self._ready()
        node = nodes(place)[0] if recipient is None else int(recipient[1:], 16)
        sender = HUB_NUM if by_hub else node
        self.api.receive(Packet(node, BROADCAST, next(self.ids), NodeInfo("Ada Lovelace", "ADA")))
        post = text_packet(sender, text, packet_id=next(self.ids), to=node if by_hub else HUB_NUM)
        self.api.receive(post)
        return post

    def posted(self, place: int) -> list[str]:
        return [sent.text for sent in self.api.texts if sent.to in nodes(place)]


class TestMeshtasticContract(ExtensionContract):
    def make_driver(self) -> ContractDriver[Any]:
        return MeshtasticDriver()


class TestMeshtasticDmContract(ExtensionContract):
    def make_driver(self) -> ContractDriver[Any]:
        return MeshtasticDmDriver()
