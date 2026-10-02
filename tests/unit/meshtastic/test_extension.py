"""The Meshtastic extension over the fake node: channel and `dm` endpoints, authors, parts, ACKs,
retries, keys, last heard."""

import asyncio
import base64
import contextlib
import itertools
import logging
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
    AddContact,
    NodeEntry,
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
CAROL = 0x00C0FFEE
"""A node of no `dm` endpoint."""
ADA_ID, BOB_ID, CAROL_ID = "!a1b2c3d4", "!0badc0de", "!00c0ffee"
ADA_KEY, NEW_KEY = b"k" * 32, b"n" * 32
LONGFAST = EndpointRef(INSTANCE, "longfast")
FAMILY = EndpointRef(INSTANCE, "family.channel")
RADIO = EndpointRef(INSTANCE, "family.radio")
STREET = EndpointRef(INSTANCE, "street.radio")
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
    ack=0.2,
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


@contextlib.asynccontextmanager
async def started(api: FakeMeshApi, hub: FakeHub) -> AsyncIterator[MeshtasticExtension]:
    """The extension of `ENDPOINTS`, started over `api` as a test has set it up."""
    extension = make(api, hub)
    await extension.start()
    try:
        await eventually(lambda: extension.ready)
        yield extension
    finally:
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


def nodeinfo(
    sender: int, long_name: str, short_name: str, *, packet_id: int = 500, key: bytes = ADA_KEY
) -> Packet:
    return Packet(sender, BROADCAST, packet_id, NodeInfo(long_name, short_name, key))


def ada(text: str, *, channel: int = 1, packet_id: int = 1001) -> Packet:
    return text_packet(ADA, text, packet_id=packet_id, channel=channel)


def dm(sender: int, text: str, *, packet_id: int = 2001) -> Packet:
    return text_packet(sender, text, packet_id=packet_id, to=HUB_NUM)


def position(sender: int, *, packet_id: int = 3001) -> Packet:
    return Packet(sender, BROADCAST, packet_id, Other("POSITION_APP"), channel=0)


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
        text_packet(CAROL, "from a node of no dm endpoint", packet_id=9, to=HUB_NUM),
        text_packet(ADA, "to another node", packet_id=10, to=BOB),
        replace(text_packet(ADA, "not PKI", packet_id=12, to=HUB_NUM), pki=False),
        replace(text_packet(ADA, "👍", packet_id=13, to=HUB_NUM), payload=Text("👍", True)),
        Packet(ADA, BROADCAST, 11, Other("POSITION_APP"), channel=1),
        nodeinfo(ADA, "Ada", "ADA"),
    ],
    ids=[
        "reaction",
        "blank",
        "other channel",
        "direct from an unlisted node",
        "direct to another node",
        "direct without PKI",
        "direct reaction",
        "position",
        "nodeinfo",
    ],
)
async def test_submits_only_texts_at_its_endpoints(
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
    api.receive(Packet(CAROL, HUB_NUM, 14, Routing(), request_id=78))
    api.receive(Packet(ADA, BOB, 15, Other("")))  # a direct message to another node

    await eventually(lambda: len(hub.heard_accounts) == 6)

    assert hub.heard_accounts == [
        Heard(account(ADA), FAMILY),
        Heard(account(BOB, "Bob", "BOB"), LONGFAST),
        Heard(account(ADA), RADIO),
        Heard(account(BOB, "Bob", "BOB"), None),
        Heard(account(CAROL), None),
        Heard(account(ADA), None),
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
    await extension.deliver(RADIO, outbound("і ще", recipient=ADA_ID))
    await extension.stop()

    assert len(times) == 4
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


# Direct messages: reading.


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_submits_a_direct_message_from_a_node_of_a_dm_endpoint(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.receive(nodeinfo(ADA, "Наталія Адамчук", "NAT"))
    api.receive(dm(ADA, "Привіт, хабе", packet_id=0x7D1))

    [message] = await hub.wait_for_submissions(1)

    assert message.endpoint == RADIO
    assert message.text == "Привіт, хабе"
    assert message.transport_id == "!a1b2c3d4/000007d1"
    assert message.author == account(ADA, "Наталія Адамчук", "NAT")
    assert message.from_recipient == ADA_ID


@pytest.mark.asyncio
async def test_a_node_of_several_dm_endpoints_is_read_at_the_first(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    running.set_endpoints(
        endpoints(street_radio={"dm": [BOB_ID]}, family_radio={"dm": [ADA_ID, BOB_ID]})
    )

    api.receive(dm(BOB, "від Боба"))
    api.receive(dm(ADA, "від Ади", packet_id=2002))

    bob, ada = await hub.wait_for_submissions(2)
    assert (bob.endpoint, bob.from_recipient) == (STREET, BOB_ID)
    assert (ada.endpoint, ada.from_recipient) == (RADIO, ADA_ID)


@pytest.mark.asyncio
async def test_submits_nothing_from_a_node_whose_dm_endpoint_was_removed(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    running.set_endpoints(endpoints(longfast={"channel": "LongFast"}))

    api.receive(dm(ADA, "removed"))
    api.receive(text_packet(BOB, "marker", packet_id=99, channel=0))

    [message] = await hub.wait_for_submissions(1)
    await settle()
    assert (message.text, len(hub.submitted)) == ("marker", 1)


@pytest.mark.asyncio
@pytest.mark.usefixtures("running")
async def test_logs_the_direct_messages_it_drops(
    api: FakeMeshApi, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, "chatko.extensions.lab"):
        api.receive(dm(CAROL, "хто тут?"))
        api.receive(replace(dm(ADA, "без PKI"), pki=False))

        await eventually(lambda: len(caplog.records) >= 2)

    assert caplog.messages == [
        "dropped a direct message from !00c0ffee, a node of no dm endpoint",
        "dropped a direct message from !a1b2c3d4 that is not PKI-encrypted",
    ]


@pytest.mark.asyncio
async def test_a_packet_to_the_hub_is_heard_at_each_dm_endpoint_of_its_node(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    running.set_endpoints(
        endpoints(street_radio={"dm": [ADA_ID]}, family_radio={"dm": [ADA_ID, BOB_ID]})
    )

    api.receive(Packet(ADA, HUB_NUM, 12, Routing(), request_id=77))  # an ACK

    await eventually(lambda: len(hub.heard_accounts) == 2)
    assert hub.heard_accounts == [Heard(account(ADA), STREET), Heard(account(ADA), RADIO)]


# Direct messages: delivering.


@pytest.mark.asyncio
async def test_sends_each_node_its_own_direct_message(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    to_ada = await running.deliver(RADIO, outbound(recipient=ADA_ID))
    to_bob = await running.deliver(RADIO, outbound(recipient=BOB_ID))

    assert to_ada == to_bob == Delivered()
    assert [(sent.text, sent.to, sent.channel, sent.want_ack) for sent in api.texts] == [
        ("NatAda: Привіт усім", ADA, 0, True),
        ("NatAda: Привіт усім", BOB, 0, True),
    ]


@pytest.mark.asyncio
async def test_a_direct_message_is_delivered_by_the_nodes_ack_not_the_brokers_echo(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.ack_texts = False
    echoed = asyncio.create_task(running.deliver(RADIO, outbound(recipient=ADA_ID)))
    await eventually(lambda: len(api.texts) == 1)
    api.answer(api.texts[0].packet_id, by=HUB_NUM)  # the implicit ACK

    acked = asyncio.create_task(running.deliver(RADIO, outbound(recipient=BOB_ID)))
    await eventually(lambda: len(api.texts) == 2)
    api.answer(api.texts[1].packet_id, by=HUB_NUM)
    api.answer(api.texts[1].packet_id, by=BOB)

    assert await echoed == Retry("!a1b2c3d4 did not acknowledge the message")
    assert await acked == Delivered()


@pytest.mark.asyncio
async def test_retries_a_direct_message_the_node_dropped(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.ack_texts = False

    result = await running.deliver(RADIO, outbound(recipient=ADA_ID))

    assert result == Retry("the node dropped the message to !a1b2c3d4")


@pytest.mark.asyncio
async def test_counts_the_parts_of_a_direct_message_per_node(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    text = " ".join(["слово"] * 30)  # two parts
    take_text = api.take_text

    async def lose_adas_second_part_once(connection: Any, sent: Any) -> int:
        api.ack_texts = (sent.to, sent.text[:12]) != (ADA, "NatAda (2/2)") or any(
            earlier.to == ADA and earlier.text == sent.text for earlier in api.texts
        )
        return await take_text(connection, sent)

    api.take_text = lose_adas_second_part_once  # type: ignore[method-assign]

    first = await running.deliver(RADIO, outbound(text, recipient=ADA_ID))
    to_bob = await running.deliver(RADIO, outbound(text, recipient=BOB_ID))
    again = await running.deliver(RADIO, replace(outbound(text, recipient=ADA_ID), attempt=2))

    assert first == Retry("the node dropped part 2 of 2 to !a1b2c3d4")
    assert to_bob == again == Delivered()
    assert [(sent.to, sent.text[:12]) for sent in api.texts] == [
        (ADA, "NatAda (1/2)"),
        (ADA, "NatAda (2/2)"),
        (BOB, "NatAda (1/2)"),
        (BOB, "NatAda (2/2)"),
        (ADA, "NatAda (2/2)"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("nak", [True, False], ids=["MAX_RETRANSMIT", "no ACK"])
async def test_a_node_that_is_away_is_tried_again_once_it_is_heard(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub, nak: bool
) -> None:
    await eventually(lambda: len(hub.retries) == 3)  # the node was ready
    if nak:
        api.naks[ADA] = "MAX_RETRANSMIT"
    else:
        api.ack_texts = False
        delivery = asyncio.create_task(running.deliver(RADIO, outbound(recipient=ADA_ID)))
        await eventually(lambda: bool(api.texts))
        api.answer(api.texts[0].packet_id, by=HUB_NUM)  # the broker has it, but no more

    result = await (running.deliver(RADIO, outbound(recipient=ADA_ID)) if nak else delivery)
    await settle()
    asked = hub.retries[3:]
    api.receive(position(BOB))
    api.receive(position(ADA))
    api.receive(position(ADA, packet_id=3002))
    await settle()

    assert result == (
        Retry("!a1b2c3d4 did not acknowledge the message (MAX_RETRANSMIT): it is away")
        if nak
        else Retry("!a1b2c3d4 did not acknowledge the message")
    )
    assert asked == []
    assert hub.retries[3:] == [RetryRequest(RADIO, ADA_ID)]


@pytest.mark.asyncio
async def test_a_node_without_a_key_is_tried_again_once_the_hubs_node_has_one(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    await eventually(lambda: len(hub.retries) == 3)
    api.naks[ADA] = "PKI_SEND_FAIL_PUBLIC_KEY"

    result = await running.deliver(RADIO, outbound(recipient=ADA_ID))
    api.receive(position(ADA))  # heard, but still without a key
    api.receive(nodeinfo(ADA, "Ada", "ADA", key=b""))
    await settle()
    asked = hub.retries[3:]
    api.receive(nodeinfo(ADA, "Ada", "ADA", packet_id=501))  # with its key
    await settle()

    assert result == Retry(
        "the hub's node has no public key of !a1b2c3d4 yet (PKI_SEND_FAIL_PUBLIC_KEY)"
    )
    assert asked == []
    assert hub.retries[3:] == [RetryRequest(RADIO, ADA_ID)]


@pytest.mark.asyncio
async def test_waits_for_each_node_at_each_endpoint(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    running.set_endpoints(
        endpoints(street_radio={"dm": [ADA_ID]}, family_radio={"dm": [ADA_ID, BOB_ID]})
    )
    await eventually(lambda: len(hub.retries) == 3)
    api.naks.update({ADA: "MAX_RETRANSMIT", BOB: "PKI_SEND_FAIL_PUBLIC_KEY"})
    await running.deliver(STREET, outbound(recipient=ADA_ID))
    await running.deliver(RADIO, outbound(recipient=ADA_ID))
    await running.deliver(RADIO, outbound(recipient=BOB_ID))

    api.receive(position(ADA))
    api.receive(position(BOB))
    await settle()

    assert hub.retries[3:] == [RetryRequest(STREET, ADA_ID), RetryRequest(RADIO, ADA_ID)]


@pytest.mark.asyncio
async def test_a_node_that_did_not_know_the_hubs_key_is_tried_again_after_the_backoff(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    await eventually(lambda: len(hub.retries) == 3)
    api.naks[ADA] = "PKI_UNKNOWN_PUBKEY"

    result = await running.deliver(RADIO, outbound(recipient=ADA_ID))
    api.receive(position(ADA))
    await settle()

    assert result == Retry(
        "!a1b2c3d4 did not know the hub's key (PKI_UNKNOWN_PUBKEY); the node sent it"
    )
    assert hub.retries[3:] == []
    assert hub.notices == []


@pytest.mark.asyncio
async def test_retries_what_the_node_gave_up_on_for_another_reason(
    running: MeshtasticExtension, api: FakeMeshApi
) -> None:
    api.naks[ADA] = "TIMEOUT"

    result = await running.deliver(RADIO, outbound(recipient=ADA_ID))

    assert result == Retry("the node gave up on the message to !a1b2c3d4: TIMEOUT")


@pytest.mark.asyncio
async def test_tells_the_admin_when_a_node_cannot_decrypt_the_hubs_messages(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    api.naks[ADA] = "NO_CHANNEL"
    first = await running.deliver(RADIO, outbound(recipient=ADA_ID))
    await running.deliver(RADIO, replace(outbound(recipient=ADA_ID), attempt=2))
    told = list(hub.notices)
    del api.naks[ADA]  # the keys were set right
    await running.deliver(RADIO, replace(outbound(recipient=ADA_ID), attempt=3))
    api.naks[ADA] = "NO_CHANNEL"  # and went wrong again
    await running.deliver(RADIO, outbound(recipient=ADA_ID, message_id="m2"))

    assert first == Retry("!a1b2c3d4 cannot decrypt the hub's direct messages (NO_CHANNEL)")
    [notice] = told
    assert notice.key == "key-mismatch:!a1b2c3d4"
    assert notice.text.startswith(
        "The radio !a1b2c3d4 cannot decrypt the direct messages of the Meshtastic node of lab "
        "(NO_CHANNEL)"
    )
    hub_key = base64.b64encode(api.settings.public_key).decode()
    assert f"If the hub's node has a new key ({hub_key})" in notice.text
    assert len(hub.notices) == 2


@pytest.mark.asyncio
async def test_tells_the_admin_once_when_a_node_announces_another_key(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.nodes[ADA] = NodeEntry(ADA, "Ada", "ADA", ADA_KEY, favorite=True)
    api.nodes[CAROL] = NodeEntry(CAROL, "Carol", "CAR", ADA_KEY)
    async with started(api, hub):
        api.receive(nodeinfo(ADA, "Ada", "ADA"))  # the same key
        api.receive(nodeinfo(ADA, "Ada", "ADA", key=b""))
        api.receive(nodeinfo(CAROL, "Carol", "CAR", key=NEW_KEY))  # of no dm endpoint
        api.receive(nodeinfo(ADA, "Ada", "ADA", key=NEW_KEY))
        api.receive(nodeinfo(ADA, "Ada", "ADA", key=NEW_KEY, packet_id=501))
        await settle()

    [notice] = hub.notices
    assert notice.key == "key-mismatch:!a1b2c3d4"
    assert notice.text.startswith("The radio !a1b2c3d4 (Ada) announces a new public key")
    assert f'"!a1b2c3d4": "{base64.b64encode(NEW_KEY).decode()}"' in notice.text


@pytest.mark.asyncio
async def test_keeps_the_nodes_of_its_dm_endpoints_as_favorites(
    api: FakeMeshApi, hub: FakeHub
) -> None:
    api.nodes[ADA] = NodeEntry(ADA, "Ada", "ADA", ADA_KEY)  # learned, not a favorite
    api.nodes[CAROL] = NodeEntry(CAROL, "Carol", "CAR", NEW_KEY)
    async with started(api, hub) as extension:
        await eventually(lambda: api.nodes[ADA].favorite)
        api.receive(nodeinfo(BOB, "Bob", "BOB", key=NEW_KEY))  # its key is learned now
        await eventually(lambda: BOB in api.nodes)
        extension.set_endpoints(endpoints(family_radio={"dm": [CAROL_ID]}))
        await eventually(lambda: api.nodes[CAROL].favorite)

    contacts = [command for command in api.admin if isinstance(command, AddContact)]
    assert contacts == [
        AddContact(ADA, ADA_KEY, "Ada", "ADA"),
        AddContact(BOB, NEW_KEY, "Bob", "BOB"),
        AddContact(CAROL, NEW_KEY, "Carol", "CAR"),
    ]


# The node.


@pytest.mark.asyncio
async def test_asks_for_the_waiting_deliveries_whenever_the_node_is_ready(
    running: MeshtasticExtension, api: FakeMeshApi, hub: FakeHub
) -> None:
    await eventually(lambda: len(hub.retries) == 3)

    api.drop()  # the node reboots
    await eventually(lambda: len(hub.retries) == 6)

    assert (
        hub.retries
        == [
            RetryRequest(LONGFAST, None),
            RetryRequest(FAMILY, None),
            RetryRequest(RADIO, None),
        ]
        * 2
    )


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
