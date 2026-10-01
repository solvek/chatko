"""The test kit beyond what the contract suite exercises: the fake hub, network and extension."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from chatko.extension_api import (
    Account,
    AccountKey,
    Delivered,
    DeliveryReport,
    EndpointRef,
    InboundMessage,
    MessageId,
    OutboundMessage,
)
from chatko.extension_api.testing import (
    FakeConfig,
    FakeEndpointConfig,
    FakeExtension,
    FakeHub,
    FakeNetwork,
    Heard,
    Notice,
    RetryRequest,
)

FAMILY = EndpointRef("fake", "family.fake")
STREET = EndpointRef("fake", "street.fake")
ADA = Account(AccountKey("fake", "ada"), "Ada Lovelace")


def extension(network: FakeNetwork, *, max_text: int | None = None) -> FakeExtension:
    ext = FakeExtension("fake", FakeConfig(max_text=max_text), FakeHub(), network=network)
    ext.set_endpoints({FAMILY: FakeEndpointConfig(place="family")})
    return ext


def outbound(text: str) -> OutboundMessage:
    return OutboundMessage(MessageId("m1"), "Ada", text, FakeHub().now())


async def test_fake_hub_records_every_call() -> None:
    hub = FakeHub()
    message = InboundMessage(FAMILY, "p1", ADA, "Привіт")

    await hub.submit(message)
    await hub.heard(ADA, FAMILY)
    await hub.retry_now(FAMILY, "!a1b2c3d4")
    await hub.notify_admin("a foreign chat", key="foreign:1")

    assert hub.submitted == [message]
    assert hub.heard_accounts == [Heard(ADA, FAMILY)]
    assert hub.retries == [RetryRequest(FAMILY, "!a1b2c3d4")]
    assert hub.notices == [Notice("a foreign chat", "foreign:1")]


def test_fake_hub_clock_stands_still_until_moved() -> None:
    hub = FakeHub()
    start = hub.now()

    hub.time += timedelta(minutes=5)

    assert hub.now() - start == timedelta(minutes=5)


async def test_waiting_for_submissions_that_do_not_come_times_out() -> None:
    hub = FakeHub(wait_timeout=0.01)
    await hub.submit(InboundMessage(FAMILY, "p1", ADA, "one"))

    with pytest.raises(TimeoutError, match=r"1 of 2 messages were submitted within 0\.01 s"):
        await hub.wait_for_submissions(2)


def test_network_texts_by_one_account() -> None:
    network = FakeNetwork()
    network.post("family", ADA, "from Ada")
    network.post("family", Account(AccountKey("fake", "bob")), "from Bob")
    network.post("street", ADA, "elsewhere")

    assert network.texts("family") == ["from Ada", "from Bob"]
    assert network.texts("family", by=ADA.key) == ["from Ada"]


def test_unsubscribing_a_stranger_is_harmless() -> None:
    FakeNetwork().unsubscribe(print)


async def test_fake_extension_cuts_a_text_longer_than_its_limit() -> None:
    network = FakeNetwork()

    result = await extension(network, max_text=10).deliver(FAMILY, outbound("Добрий вечір"))

    assert result == Delivered(truncated=True)
    assert network.texts("family") == ["Ada: Добр…"]


async def test_fake_extension_keeps_a_text_within_its_limit() -> None:
    network = FakeNetwork()

    result = await extension(network, max_text=10).deliver(FAMILY, outbound("Так"))

    assert result == Delivered()
    assert network.texts("family") == ["Ada: Так"]


async def test_fake_extension_records_delivery_reports() -> None:
    ext = extension(FakeNetwork())
    report = DeliveryReport(FAMILY, "p1", STREET, None, Delivered(truncated=True))

    await ext.delivery_report(report)

    assert ext.reports == [report]


def test_fake_extension_rejects_two_endpoints_at_one_place() -> None:
    ext = extension(FakeNetwork())
    place = FakeEndpointConfig(place="family")

    with pytest.raises(ValueError, match="same place 'family'"):
        ext.set_endpoints({FAMILY: place, STREET: place})


def test_fake_endpoint_config_needs_a_place() -> None:
    with pytest.raises(ValidationError):
        FakeEndpointConfig(place="")


async def test_fake_extension_reads_a_place_with_recipients_only_from_them() -> None:
    hub, network = FakeHub(), FakeNetwork()
    ext = FakeExtension("fake", FakeConfig(), hub, network=network)
    ext.set_endpoints({FAMILY: FakeEndpointConfig(place="family", recipients=("!a1",))})
    await ext.start()

    network.post("family", ADA, "stranger", recipient="!zz")
    network.post("family", ADA, "listed", recipient="!a1")
    [message] = await hub.wait_for_submissions(1)
    await ext.stop()

    assert (message.text, message.from_recipient) == ("listed", "!a1")
