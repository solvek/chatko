"""The Meshtastic adapter against the docker lab: MeshNode over LibraryMeshApi, driving the lab's
hub node and its radio node, which reach each other only through the lab's Mosquitto (spike S2).

Opt-in: start the lab, then run these tests on their own; the default run deselects them.

    docker compose -f lab/docker-compose.yml up -d
    uv run pytest -m lab tests/integration/test_meshtastic_lab.py

The settings are those of `lab/provision.py`, so the two never undo each other's work. Some tests
stop and restart the lab's containers, and the first one reboots the hub node twice: the whole
module takes a few minutes.
"""

import base64
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from chatko_meshtastic.api import BROADCAST, Text, node_id
from chatko_meshtastic.node import Ack, Nak
from tests.integration.lab import (
    FAMILY,
    HUB,
    PUBLIC_KEYS,
    RADIO,
    READY_WITHIN,
    Lab,
    b64,
    compose,
    lab_config,
    lab_is_up,
)

pytestmark = pytest.mark.lab


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


async def test_a_provisioned_node_is_served_without_a_reboot(hub: Lab, radio: Lab) -> None:
    assert hub.api.connections == 1
    assert hub.node.public_key == base64.b64decode(PUBLIC_KEYS[HUB])
    assert radio.node.public_key == base64.b64decode(PUBLIC_KEYS[RADIO])
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
