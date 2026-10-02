"""The whole hub against the docker lab: `chatko run`'s composition root with real SQLite, the
Meshtastic extension on the lab's hub node and the Telegram extension over its fake, relaying
between Telegram chats and the lab's radio node, which a MeshNode drives as a member would their
radio. The radio reaches the hub's node only through the lab's Mosquitto (spike S2).

Opt-in, like the adapter's lab tests (`test_meshtastic_lab.py`):

    docker compose -f lab/docker-compose.yml up -d
    uv run pytest -m lab tests/integration

Retries wait an hour here, so a message that reaches the radio after a retry was asked for by the
extension (a node heard again, a node ready again), never by the outbox's backoff. Some tests
stop the radio's container or give the radio another key; they put it back as they found it.
"""

import asyncio
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml

from chatko.app.run import DATABASE_FILE, run_hub
from chatko.application.outbox import OutboxSettings
from chatko.application.runtime import HubSettings
from chatko.extension_api import HubContext
from chatko_meshtastic import MeshtasticExtension
from chatko_meshtastic.api import BROADCAST, Packet, Text, node_id
from chatko_meshtastic.node import Ack
from chatko_telegram import TelegramExtension
from chatko_telegram.api import Chat, ChatKind
from chatko_telegram.config import TelegramConfig
from chatko_telegram.testing import FakeTelegramApi
from tests.integration.lab import (
    FAMILY,
    HUB,
    RADIO,
    READY_WITHIN,
    Lab,
    b64,
    compose,
    lab_config,
    lab_is_up,
    lab_key,
    lab_settings,
    wait_until,
)

pytestmark = pytest.mark.lab

_log = logging.getLogger("tests.lab")

BOT = 123456
FAMILY_CHAT = Chat(-1001, ChatKind.SUPERGROUP, "Family")
DIRECT_CHAT = Chat(-1002, ChatKind.SUPERGROUP, "Direct")
OWNER = 777
NAT = 111
RADIO_ID = node_id(RADIO)

SETTINGS = HubSettings(
    outbox=OutboxSettings(first_retry=timedelta(hours=1), longest_retry=timedelta(hours=1))
)
"""No retry by the backoff while a test runs."""

ACKED_WITHIN = 30.0
"""Seconds for a direct message to reach the radio, with the radio's ACK, from Telegram."""


def hub_config(radio_key: str) -> dict[str, Any]:
    """Two groups, each a Telegram chat and the radio: `family` on the lab's private channel,
    `direct` by direct message to the radio, whose public key is `radio_key`."""
    lab = lab_settings("hub", contacts={RADIO_ID: radio_key})
    return {
        "extensions": {
            "telegram": {"type": "telegram", "bot_token": f"{BOT}:lab"},
            "lab": {"type": "meshtastic", **lab},
        },
        "groups": {
            "family": {
                "sites": {
                    "telegram": {"ext": "telegram", "chat": FAMILY_CHAT.id},
                    "channel": {"ext": "lab", "channel": "Family"},
                }
            },
            "direct": {
                "sites": {
                    "telegram": {"ext": "telegram", "chat": DIRECT_CHAT.id},
                    "radio": {"ext": "lab", "dm": [RADIO_ID]},
                }
            },
        },
        "sources": {"owner": {"ext": "telegram", "chat": OWNER}},
        "admin_notices": {"to": "owner"},
        "people": {"NatAda": [f"telegram:{NAT}"], "Radio": [f"meshtastic:{RADIO_ID}"]},
    }


@dataclass
class Hub:
    """The hub as `chatko run` runs it, with Telegram in memory and the Meshtastic instances it
    started, the latest last."""

    config: Path
    data: Path
    telegram: FakeTelegramApi = field(default_factory=FakeTelegramApi)
    meshtastic: list[MeshtasticExtension] = field(default_factory=list)

    def types(self) -> Mapping[str, type[Any]]:
        hub = self

        class Telegram(TelegramExtension):
            def __init__(self, instance: str, config: TelegramConfig, context: HubContext) -> None:
                super().__init__(instance, config, context, api=hub.telegram)

        class Meshtastic(MeshtasticExtension):
            async def start(self) -> None:
                await super().start()
                hub.meshtastic.append(self)

        return {"telegram": Telegram, "meshtastic": Meshtastic}

    def write(self, config: Mapping[str, Any]) -> None:
        self.config.write_text(yaml.safe_dump(dict(config), allow_unicode=True), encoding="utf-8")

    @property
    def ready(self) -> bool:
        return bool(self.meshtastic) and self.meshtastic[-1].ready

    def post(self, chat: Chat, text: str) -> None:
        self.telegram.post(chat, NAT, text, name="Наталія Адамчук")

    def deliveries(self, endpoint: str) -> list[tuple[str, str | None]]:
        """The state and last error of each delivery to an endpoint, oldest first."""
        with sqlite3.connect(self.data / DATABASE_FILE) as db:
            rows = db.execute(
                "SELECT state, last_error FROM deliveries WHERE endpoint = ? ORDER BY seq",
                (endpoint,),
            )
            return [(row[0], row[1]) for row in rows]

    def states(self, endpoint: str) -> list[str]:
        return [state for state, _ in self.deliveries(endpoint)]


@asynccontextmanager
async def running(hub: Hub) -> AsyncIterator[asyncio.Task[int]]:
    """The hub runs inside the block, its node ready, and is stopped at its end."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_hub(hub.config, {}, hub.types(), data=hub.data, stop=stop, settings=SETTINGS)
    )
    try:
        await wait_until(lambda: hub.ready or task.done(), within=READY_WITHIN)
        assert not task.done(), "the hub did not start"
        yield task
    finally:
        stop.set()
        assert await asyncio.wait_for(task, 30) == 0


@pytest_asyncio.fixture
async def radio() -> AsyncIterator[Lab]:
    lab_is_up()
    lab = Lab(lab_config("radio"))
    await lab.start()
    yield lab
    await lab.node.stop()


@pytest_asyncio.fixture
async def hub(radio: Lab, tmp_path: Path) -> AsyncIterator[Hub]:
    key = radio.node.public_key
    assert key
    (tmp_path / "config").mkdir()
    hub = Hub(tmp_path / "config" / "chatko.yaml", tmp_path / "data")
    hub.write(hub_config(b64(key)))
    async with running(hub):
        yield hub


def direct_to_radio(packet: Packet) -> bool:
    return packet.sender == HUB and packet.to == RADIO and packet.pki


async def test_a_telegram_message_reaches_the_radio_on_the_channel(hub: Hub, radio: Lab) -> None:
    hub.post(FAMILY_CHAT, "Привіт з Телеграму")

    got = await radio.text(lambda packet: packet.sender == HUB, within=30)
    assert (got.to, got.channel, got.via_mqtt) == (BROADCAST, FAMILY, True)
    assert got.payload == Text("NatAda: Привіт з Телеграму")
    await wait_until(lambda: hub.deliveries("family.channel") == [("delivered", None)], within=10)


async def test_a_channel_text_from_the_radio_reaches_telegram(hub: Hub, radio: Lab) -> None:
    await radio.node.send_text("Привіт з радіо", channel=FAMILY)

    await wait_until(
        lambda: hub.telegram.texts(FAMILY_CHAT.id) == ["Radio: Привіт з радіо"], within=20
    )
    assert hub.telegram.texts(DIRECT_CHAT.id) == []


async def test_a_telegram_message_reaches_the_radio_as_a_direct_message(
    hub: Hub, radio: Lab
) -> None:
    posted = time.monotonic()
    hub.post(DIRECT_CHAT, "Привіт напряму")

    got = await radio.text(direct_to_radio, within=ACKED_WITHIN)
    assert got.payload == Text("NatAda: Привіт напряму")
    await wait_until(
        lambda: hub.deliveries("direct.radio") == [("delivered", None)], within=ACKED_WITHIN
    )
    _log.info("from Telegram to the radio's ACK: %.1f s", time.monotonic() - posted)


async def test_a_long_message_reaches_the_radio_part_by_part_and_telegram_shows_the_cut(
    hub: Hub, radio: Lab
) -> None:
    text = " ".join(["слово"] * 120)  # 1319 bytes: more than three parts hold
    hub.post(DIRECT_CHAT, text)

    parts = [await radio.text(direct_to_radio, within=ACKED_WITHIN) for _ in range(3)]
    texts = [part.payload.text for part in parts if isinstance(part.payload, Text)]
    assert [part.split(":")[0] for part in texts] == [
        "NatAda (1/3)",
        "NatAda (2/3)",
        "NatAda (3/3)",
    ]
    assert texts[-1].endswith("…")
    await wait_until(lambda: len(hub.telegram.reactions) == 1, within=ACKED_WITHIN)
    assert hub.telegram.reactions[0].emoji == "✍"


async def test_a_direct_message_from_the_radio_reaches_telegram(hub: Hub, radio: Lab) -> None:
    sent = await radio.node.send_text("Відповідь з радіо", to=HUB)

    assert await sent.outcome(within=30) == Ack(HUB)
    await wait_until(lambda: hub.telegram.texts(DIRECT_CHAT.id) == ["Radio: Відповідь з радіо"], 20)
    assert hub.telegram.texts(FAMILY_CHAT.id) == []


async def test_a_direct_message_waits_for_a_radio_that_is_away_until_it_is_heard(
    hub: Hub, radio: Lab
) -> None:
    await radio.node.stop()
    compose("stop", "radio")
    try:
        hub.post(DIRECT_CHAT, "Поки тебе не було")
        await wait_until(lambda: "MAX_RETRANSMIT" in str(hub.deliveries("direct.radio")), within=60)
    finally:
        compose("start", "radio")
    back = Lab(lab_config("radio"))
    await back.start()
    try:
        # A radio restarted within 10 minutes of its last NodeInfo sends none at boot (spike
        # S2), so the hub hears it when it sends something: here a text on the primary
        # channel, which is no endpoint.
        await back.node.send_text("Я повернувся", channel=0)
        got = await back.text(direct_to_radio, within=ACKED_WITHIN)
        assert got.payload == Text("NatAda: Поки тебе не було")
        await wait_until(lambda: hub.states("direct.radio") == ["delivered"], ACKED_WITHIN)
    finally:
        await back.node.stop()


async def test_a_reset_radio_gets_its_messages_once_the_admin_gives_its_new_key(
    hub: Hub, radio: Lab
) -> None:
    lab_key_of_radio = radio.node.public_key
    assert lab_key_of_radio
    await radio.node.stop()
    reset = Lab(lab_config("radio", private_key=lab_key("RESET")))
    try:
        await reset.start()  # the radio's new key pair, as after a factory reset
        new_key = reset.node.public_key
        assert new_key
        hub.post(DIRECT_CHAT, "Після скидання")

        await wait_until(lambda: notices(hub, RADIO_ID) != [], within=60)
        assert hub.states("direct.radio") == ["pending"]
        await reload(hub, hub_config(b64(new_key)))  # the admin gives the new key

        got = await reset.text(direct_to_radio, within=ACKED_WITHIN)
        assert got.payload == Text("NatAda: Після скидання")
        await wait_until(lambda: hub.states("direct.radio") == ["delivered"], within=10)
    finally:
        await reset.node.stop()
        restored = Lab(lab_config("radio"))
        await restored.start()
        await restored.node.stop()
        await reload(hub, hub_config(b64(lab_key_of_radio)))
    assert len(notices(hub, RADIO_ID)) == 1


async def reload(hub: Hub, config: Mapping[str, Any]) -> None:
    """Change the config, and wait until the hub has a new Meshtastic instance, ready."""
    before = len(hub.meshtastic)
    hub.write(config)
    await wait_until(lambda: len(hub.meshtastic) > before and hub.ready, within=READY_WITHIN)


def notices(hub: Hub, about: str) -> list[str]:
    return [text for text in hub.telegram.texts(OWNER) if about in text]
