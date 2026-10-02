"""The Meshtastic extension over the fake node: channel endpoints, authors, parts, last heard."""

import asyncio
import base64
import itertools
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio

from chatko.extension_api import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Delivered,
    EndpointRef,
    Failed,
    MessageId,
    OutboundMessage,
    Retry,
)
from chatko.extension_api.testing import FakeHub
from chatko.extension_api.testing.hub import Heard, RetryRequest
from chatko_meshtastic import (
    MeshtasticConfig,
    MeshtasticEndpoint,
    MeshtasticExtension,
    MeshtasticTimings,
)
from chatko_meshtastic import extension as extension_module
from chatko_meshtastic.api import (
    BROADCAST,
    NodeInfo,
    Other,
    Packet,
    RejectedError,
    Routing,
    Text,
    UnreachableError,
)
from chatko_meshtastic.node import NodeTimings
from chatko_meshtastic.testing import HUB_NUM, FakeMeshApi, text_packet
from chatko_meshtastic.text import MAX_PACKET_BYTES

INSTANCE = "lab"
ADA = 0xA1B2C3D4
BOB = 0x0BADC0DE
LONGFAST = EndpointRef(INSTANCE, "longfast")
FAMILY = EndpointRef(INSTANCE, "family.channel")
RADIO = EndpointRef(INSTANCE, "family.radio")
CONFIG = MeshtasticConfig.model_validate(
    {
        "connection": {"tcp": "meshtasticd:4403"},
        "long_name": "chatko",
        "short_name": "CHKO",
        "region": "EU_868",
        "mqtt": {"host": "mosquitto", "root_topic": "msh/lab"},
        "channels": [
            {"name": "LongFast", "psk": "AQ=="},
            {"name": "Family", "psk": base64.b64encode(bytes(16)).decode()},
            {"name": "Spare", "psk": "AQ=="},
        ],
        "min_send_interval_s": 2,
    }
)
TIMINGS = MeshtasticTimings(
    NodeTimings(
        min_send_interval=0.0,
        admin_timeout=0.2,
        reboot_timeout=0.3,
        reconnect=(0.005, 0.02),
        unreachable_notice=0.05,
    ),
    echo=0.2,
    ready=0.5,
    heard=60.0,
)


def endpoints(**places: dict[str, Any]) -> dict[EndpointRef, MeshtasticEndpoint]:
    return {
        EndpointRef(INSTANCE, name.replace("_", ".")): MeshtasticEndpoint.model_validate(place)
        for name, place in places.items()
    }


ENDPOINTS = endpoints(
    longfast={"channel": "LongFast"},
    family_channel={"channel": "Family"},
    family_radio={"dm": ["!a1b2c3d4", "!0badc0de"]},
)


@pytest.fixture
def api() -> FakeMeshApi:
    return FakeMeshApi()


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


def make(
    api: FakeMeshApi, hub: FakeHub, timings: MeshtasticTimings = TIMINGS
) -> MeshtasticExtension:
    extension = MeshtasticExtension(INSTANCE, CONFIG, hub, api=api, timings=timings)
    extension.set_endpoints(ENDPOINTS)
    return extension


@pytest.fixture
def extension(api: FakeMeshApi, hub: FakeHub) -> MeshtasticExtension:
    return make(api, hub)


@pytest_asyncio.fixture
async def running(extension: MeshtasticExtension) -> AsyncIterator[MeshtasticExtension]:
    await extension.start()
    await eventually(lambda: extension.ready)
    yield extension
    await extension.stop()


async def eventually(check: Callable[[], bool], within: float = 2.0) -> None:
    async with asyncio.timeout(within):
        while not check():  # noqa: ASYNC110 - the extension's work shows only in the fakes
            await asyncio.sleep(0.005)


async def settle() -> None:
    await asyncio.sleep(0.05)


def outbound(
    text: str = "Привіт усім",
    *,
    label: str = "NatAda",
    message_id: str = "m1",
    recipient: str | None = None,
    attachments: tuple[Attachment, ...] = (),
) -> OutboundMessage:
    return OutboundMessage(
        MessageId(message_id),
        label,
        text,
        FakeHub().now(),
        attachments=attachments,
        recipient=recipient,
    )


def nodeinfo(sender: int, long_name: str, short_name: str, *, packet_id: int = 500) -> Packet:
    return Packet(sender, BROADCAST, packet_id, NodeInfo(long_name, short_name, b"k" * 32))


def ada(text: str, *, channel: int = 1, packet_id: int = 1001) -> Packet:
    return text_packet(ADA, text, packet_id=packet_id, channel=channel)


def account(num: int, long_name: str = "", short_name: str | None = None) -> Account:
    return Account(AccountKey("meshtastic", f"!{num:08x}"), long_name, short_name)


# Endpoints.


def test_refuses_a_channel_the_node_does_not_have(extension: MeshtasticExtension) -> None:
    with pytest.raises(
        ValueError, match=r"lab/lab\.street: the node of lab has no channel 'Street'"
    ):
        extension.set_endpoints(endpoints(lab_street={"channel": "Street"}))


def test_refuses_two_endpoints_on_one_channel(extension: MeshtasticExtension) -> None:
    with pytest.raises(ValueError, match="are the same channel"):
        extension.set_endpoints(endpoints(a={"channel": "Family"}, b={"channel": "Family"}))


def test_a_dm_endpoints_nodes_are_its_recipients(extension: MeshtasticExtension) -> None:
    assert extension.recipients(RADIO) == ("!a1b2c3d4", "!0badc0de")
    assert extension.recipients(FAMILY) == ()
    assert extension.recipients(EndpointRef(INSTANCE, "nowhere")) == ()


def test_the_timings_take_the_send_interval_from_the_config() -> None:
    assert MeshtasticTimings.from_config(CONFIG).node.min_send_interval == 2


# Reading.


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_submits_a_channel_text_with_the_nodes_names(api: FakeMeshApi, hub: FakeHub) -> None:
    api.receive(nodeinfo(ADA, "Наталія Адамчук", "NAT"))
    api.receive(ada("Привіт усім", packet_id=0x3E9))

    [message] = await hub.wait_for_submissions(1)

    assert message.endpoint == FAMILY
    assert message.text == "Привіт усім"
    assert message.transport_id == "!a1b2c3d4/000003e9"
    assert message.author == account(ADA, "Наталія Адамчук", "NAT")
    assert message.from_recipient is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_an_author_without_nodeinfo_has_only_its_node_id(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.receive(ada("хто я?"))

    [message] = await hub.wait_for_submissions(1)

    assert message.author == account(ADA)


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_the_same_packet_through_another_gateway_has_the_same_transport_id(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.receive(ada("раз"))
    api.receive(replace(ada("раз"), hop_limit=1))  # another gateway, more hops away
    api.receive(ada("два", packet_id=1002))

    first, again, other = await hub.wait_for_submissions(3)

    assert first.transport_id == again.transport_id != other.transport_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_reads_the_primary_channel_as_a_source(api: FakeMeshApi, hub: FakeHub) -> None:
    api.receive(nodeinfo(BOB, "Base Camp", "BC1"))
    api.receive(text_packet(BOB, "Всім привіт", packet_id=7, channel=0))

    [message] = await hub.wait_for_submissions(1)

    assert message.endpoint == LONGFAST
    assert message.author == account(BOB, "Base Camp", "BC1")


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_drops_its_own_packets(api: FakeMeshApi, hub: FakeHub) -> None:
    api.receive(text_packet(HUB_NUM, "echo of the hub", packet_id=5, channel=1))
    api.receive(ada("marker"))

    await hub.wait_for_submissions(1)
    await settle()

    assert [message.text for message in hub.submitted] == ["marker"]
    assert all(heard.account.key.external_id != f"!{HUB_NUM:08x}" for heard in hub.heard_accounts)


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
@pytest.mark.parametrize(
    "packet",
    [
        replace(ada("👍"), payload=Text("👍", reaction=True)),
        ada("   \n "),
        ada("on a channel that is not an endpoint", channel=2),
        text_packet(ADA, "a direct message", packet_id=9, to=HUB_NUM),
        Packet(ADA, BROADCAST, 11, Other("POSITION_APP"), channel=1),
        nodeinfo(ADA, "Ada", "ADA"),
    ],
    ids=["reaction", "blank", "other channel", "direct", "position", "nodeinfo"],
)
async def test_submits_only_texts_on_its_channels(
    api: FakeMeshApi, hub: FakeHub, packet: Packet
) -> None:
    api.receive(packet)
    api.receive(text_packet(BOB, "marker", packet_id=99, channel=1))

    await hub.wait_for_submissions(1)
    await settle()

    assert [message.text for message in hub.submitted] == ["marker"]


@pytest.mark.asyncio
async def test_submits_nothing_of_a_channel_whose_endpoint_was_removed(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    running.set_endpoints(endpoints(longfast={"channel": "LongFast"}))

    api.receive(ada("removed"))
    api.receive(text_packet(BOB, "marker", packet_id=99, channel=0))

    [message] = await hub.wait_for_submissions(1)
    await settle()
    assert (message.text, len(hub.submitted)) == ("marker", 1)


# Last heard.


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_any_packet_tells_the_hub_where_the_node_was_heard(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.receive(Packet(ADA, BROADCAST, 11, Other("POSITION_APP"), channel=1))
    api.receive(nodeinfo(BOB, "Bob", "BOB"))
    api.receive(Packet(ADA, HUB_NUM, 12, Routing(), request_id=77))  # an ACK to the hub
    api.receive(Packet(BOB, BROADCAST, 13, Other("TELEMETRY_APP"), channel=2))

    await eventually(lambda: len(hub.heard_accounts) == 4)

    assert hub.heard_accounts == [
        Heard(account(ADA), FAMILY),
        Heard(account(BOB, "Bob", "BOB"), LONGFAST),
        Heard(account(ADA), None),
        Heard(account(BOB, "Bob", "BOB"), None),
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_a_packet_it_could_not_decrypt_was_heard_at_no_endpoint(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.receive(Packet(ADA, BROADCAST, 11, Other(""), channel=1))  # 1 is a channel hash here

    await eventually(lambda: len(hub.heard_accounts) == 1)

    assert hub.heard_accounts == [Heard(account(ADA), None)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_tells_the_hub_of_a_node_at_one_place_once_a_minute(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    for packet_id in (1, 2, 3):
        api.receive(Packet(ADA, BROADCAST, packet_id, Other("POSITION_APP"), channel=1))
    api.receive(Packet(ADA, BROADCAST, 4, Other("POSITION_APP"), channel=0))
    await settle()
    told = len(hub.heard_accounts)
    hub.time += timedelta(seconds=TIMINGS.heard)
    api.receive(Packet(ADA, BROADCAST, 5, Other("POSITION_APP"), channel=1))
    await settle()

    assert told == 2
    assert [heard.endpoint for heard in hub.heard_accounts] == [FAMILY, LONGFAST, FAMILY]


# Delivering.


@pytest.mark.asyncio
async def test_posts_the_label_and_the_text_on_the_channel(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    result = await running.deliver(FAMILY, outbound())

    assert result == Delivered()
    [sent] = api.texts
    assert (sent.text, sent.to, sent.channel, sent.want_ack) == (
        "NatAda: Привіт усім",
        BROADCAST,
        1,
        True,
    )


@pytest.mark.asyncio
async def test_posts_a_placeholder_for_what_is_not_text(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    message = outbound("на річці", attachments=(Attachment(AttachmentKind.PHOTO),))

    await running.deliver(LONGFAST, message)

    assert [(sent.text, sent.channel) for sent in api.texts] == [("NatAda: [photo] на річці", 0)]


@pytest.mark.asyncio
async def test_posts_a_long_text_in_parts(running: MeshtasticExtension, api: FakeMeshApi) -> None:
    text = " ".join(["слово"] * 30)  # 359 bytes

    result = await running.deliver(FAMILY, outbound(text))

    assert result == Delivered()
    assert [sent.text.split(":")[0] for sent in api.texts] == ["NatAda (1/2)", "NatAda (2/2)"]
    assert all(len(sent.text.encode()) <= MAX_PACKET_BYTES for sent in api.texts)


@pytest.mark.asyncio
async def test_says_when_a_text_was_cut(running: MeshtasticExtension, api: FakeMeshApi) -> None:
    result = await running.deliver(FAMILY, outbound(" ".join(["слово"] * 100)))

    assert result == Delivered(truncated=True)
    assert len(api.texts) == 3
    assert api.texts[2].text.endswith("…")


@pytest.mark.asyncio
async def test_keeps_the_send_interval_between_parts(api: FakeMeshApi, hub: FakeHub) -> None:
    extension = make(api, hub, replace(TIMINGS, node=replace(TIMINGS.node, min_send_interval=0.1)))
    await extension.start()
    loop = asyncio.get_running_loop()
    times: list[float] = []
    take_text = api.take_text

    async def timed(connection: Any, sent: Any) -> int:
        times.append(loop.time())
        return await take_text(connection, sent)

    api.take_text = timed  # type: ignore[method-assign]

    await extension.deliver(FAMILY, outbound(" ".join(["слово"] * 30)))
    await extension.deliver(LONGFAST, outbound("ще"))
    await extension.stop()

    assert len(times) == 3
    assert all(later - earlier >= 0.09 for earlier, later in itertools.pairwise(times))


@pytest.mark.asyncio
async def test_waits_for_a_node_that_is_still_provisioning(
    extension: MeshtasticExtension, api: FakeMeshApi
) -> None:
    await extension.start()
    try:
        result = await extension.deliver(FAMILY, outbound())
    finally:
        await extension.stop()

    assert result == Delivered()
    assert api.connections == 2  # the reboot after provisioning


@pytest.mark.asyncio
async def test_retries_when_the_broker_does_not_echo_the_packet(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.ack_texts = False

    result = await running.deliver(FAMILY, outbound())

    assert result == Retry("the broker did not echo the message")


@pytest.mark.asyncio
async def test_retries_when_the_node_gives_up(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.ack_texts = False
    delivery = asyncio.create_task(running.deliver(FAMILY, outbound()))
    await eventually(lambda: bool(api.texts))

    api.answer(api.texts[0].packet_id, by=HUB_NUM, error="MAX_RETRANSMIT")

    assert await delivery == Retry("the node gave up on the message: MAX_RETRANSMIT")


@pytest.mark.asyncio
async def test_a_retry_goes_on_from_the_part_that_did_not_get_through(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    text = " ".join(["слово"] * 50)  # three parts
    take_text = api.take_text

    async def lose_the_second(connection: Any, sent: Any) -> int:
        api.ack_texts = len(api.texts) != 1
        return await take_text(connection, sent)

    api.take_text = lose_the_second  # type: ignore[method-assign]

    first = await running.deliver(FAMILY, outbound(text))
    second = await running.deliver(FAMILY, replace(outbound(text), attempt=2))

    assert first == Retry("the broker did not echo part 2 of 3")
    assert second == Delivered()
    assert [sent.text.split(":")[0] for sent in api.texts] == [
        "NatAda (1/3)",
        "NatAda (2/3)",
        "NatAda (2/3)",
        "NatAda (3/3)",
    ]


@pytest.mark.asyncio
async def test_a_delivered_message_is_sent_whole_again_if_asked_again(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    text = " ".join(["слово"] * 30)

    await running.deliver(FAMILY, outbound(text))
    await running.deliver(FAMILY, replace(outbound(text), attempt=2))

    assert len(api.texts) == 4


@pytest.mark.asyncio
async def test_parts_are_counted_per_endpoint(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    text = " ".join(["слово"] * 30)
    api.ack_texts = False
    await running.deliver(FAMILY, outbound(text))
    api.ack_texts = True

    await running.deliver(LONGFAST, outbound(text))

    assert [sent.channel for sent in api.texts] == [1, 0, 0]


@pytest.mark.asyncio
async def test_forgets_the_oldest_parts_in_progress(
    running: MeshtasticExtension, api: FakeMeshApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension_module, "PARTS_IN_PROGRESS", 1)
    text = " ".join(["слово"] * 30)  # two parts
    echoes = iter([True, False, True, False, True, True])
    take_text = api.take_text

    async def echo_some(connection: Any, sent: Any) -> int:
        api.ack_texts = next(echoes)
        return await take_text(connection, sent)

    api.take_text = echo_some  # type: ignore[method-assign]
    await running.deliver(FAMILY, outbound(text, message_id="m1"))
    await running.deliver(LONGFAST, outbound(text, message_id="m2"))

    assert await running.deliver(FAMILY, outbound(text, message_id="m1")) == Delivered()
    assert [sent.text.split(":")[0] for sent in api.texts[4:]] == ["NatAda (1/2)", "NatAda (2/2)"]


@pytest.mark.asyncio
async def test_retries_while_the_node_is_unreachable(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.online = False
    api.drop()
    await eventually(lambda: not running.ready)

    result = await running.deliver(FAMILY, outbound())

    assert result == Retry("the node of lab is not ready")


@pytest.mark.asyncio
async def test_retries_when_the_connection_fails_while_sending(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.text_error = UnreachableError("the connection broke")

    assert await running.deliver(FAMILY, outbound()) == Retry("the connection broke")


@pytest.mark.asyncio
async def test_fails_what_the_node_rejects(running: MeshtasticExtension, api: FakeMeshApi) -> None:
    api.text_error = RejectedError("too long")

    assert await running.deliver(FAMILY, outbound()) == Failed("too long")


@pytest.mark.asyncio
async def test_retries_while_it_is_not_running(extension: MeshtasticExtension) -> None:
    assert await extension.deliver(FAMILY, outbound()) == Retry("lab is not running")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "recipient", "reason"),
    [
        (EndpointRef(INSTANCE, "nowhere"), None, "lab/nowhere is not an endpoint of lab"),
        (FAMILY, "!a1b2c3d4", "lab/family.channel has no recipients, so not '!a1b2c3d4'"),
        (RADIO, "!00000001", "'!00000001' is not a node of lab/family.radio"),
        (RADIO, None, "lab/family.radio delivers to each of its nodes, and none was named"),
        (RADIO, "!a1b2c3d4", "direct messages are not relayed yet"),
    ],
)
async def test_fails_what_it_cannot_deliver(
    running: MeshtasticExtension,
    api: FakeMeshApi,
    endpoint: EndpointRef,
    recipient: str | None,
    reason: str,
) -> None:
    result = await running.deliver(endpoint, outbound(recipient=recipient))

    assert result == Failed(reason)
    assert api.texts == []


# The node.


@pytest.mark.asyncio
async def test_asks_for_the_waiting_deliveries_whenever_the_node_is_ready(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    await eventually(lambda: len(hub.retries) == 2)

    api.drop()  # the node reboots
    await eventually(lambda: len(hub.retries) == 4)

    assert hub.retries == [RetryRequest(LONGFAST, None), RetryRequest(FAMILY, None)] * 2


@pytest.mark.asyncio
async def test_passes_on_the_nodes_admin_notices(api: FakeMeshApi, hub: FakeHub) -> None:
    api.online = False
    extension = make(api, hub)
    await extension.start()

    await eventually(lambda: bool(hub.notices))
    await extension.stop()

    assert hub.notices[0].key == "node-unreachable"
    assert "The Meshtastic node of lab at fake node has been unreachable" in hub.notices[0].text


@pytest.mark.asyncio
async def test_connects_over_tcp_without_a_given_api(
    hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[tuple[str, int]] = []

    def connect(host: str, port: int) -> FakeMeshApi:
        made.append((host, port))
        return FakeMeshApi()

    monkeypatch.setattr(extension_module, "LibraryMeshApi", connect)
    extension = MeshtasticExtension(INSTANCE, CONFIG, hub, timings=TIMINGS)
    extension.set_endpoints(ENDPOINTS)

    await extension.start()
    await eventually(lambda: extension.ready)
    await extension.stop()

    assert made == [("meshtasticd", 4403)]
    assert not extension.ready


@pytest.mark.asyncio
async def test_stops_twice_and_without_a_start(extension: MeshtasticExtension) -> None:
    await extension.stop()
    await extension.start()
    await extension.stop()
    await extension.stop()
