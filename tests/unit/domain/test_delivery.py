from datetime import UTC, datetime, timedelta

import pytest

from chatko.domain import (
    Account,
    AccountKey,
    Author,
    Delivery,
    DeliveryState,
    DomainError,
    EndpointRef,
    Message,
    MessageId,
    Target,
)

TG = EndpointRef("tg", "chat:-100")
MESH = EndpointRef("kyiv", "channel:family")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)
MESSAGE = Message(
    MessageId("m1"), TG, "42", Author(Account(AccountKey("telegram", "1"))), "Привіт", NOW
)


def pending() -> Delivery:
    return Delivery.of(MESSAGE, Target(MESH), "NatAda", NOW)


def test_new_delivery_is_pending_and_due_now() -> None:
    delivery = pending()

    assert delivery.state is DeliveryState.PENDING
    assert delivery.attempts == 0
    assert delivery.message_id == "m1"
    assert delivery.endpoint == MESH
    assert delivery.author_label == "NatAda"
    assert delivery.text == "Привіт"
    assert delivery.is_due(NOW)
    assert not delivery.is_due(NOW - timedelta(seconds=1))


def test_target_text_replaces_the_message_text() -> None:
    assert Delivery.of(MESSAGE, Target(MESH, text="інше"), "NatAda", NOW).text == "інше"


def test_delivered_is_final() -> None:
    delivered = pending().delivered()

    assert delivered.state is DeliveryState.DELIVERED
    assert delivered.attempts == 1
    assert not delivered.truncated
    assert not delivered.is_due(LATER)
    with pytest.raises(DomainError, match="already delivered"):
        delivered.retry(LATER, "again")


def test_delivered_may_be_truncated() -> None:
    assert pending().delivered(truncated=True).truncated


def test_retry_stays_pending_until_the_given_time() -> None:
    retried = pending().retry(LATER, "node busy")

    assert retried.state is DeliveryState.PENDING
    assert retried.attempts == 1
    assert retried.last_error == "node busy"
    assert not retried.is_due(NOW)
    assert retried.is_due(LATER)


def test_attempts_add_up_and_success_clears_the_error() -> None:
    delivered = pending().retry(LATER, "a").retry(LATER, "b").delivered()

    assert delivered.attempts == 3
    assert delivered.last_error is None


def test_failed_is_final() -> None:
    failed = pending().failed("chat not found")

    assert failed.state is DeliveryState.FAILED
    assert failed.last_error == "chat not found"
    assert not failed.is_due(LATER)
    with pytest.raises(DomainError, match="already failed"):
        failed.delivered()


def test_retry_time_must_be_timezone_aware() -> None:
    with pytest.raises(DomainError, match="timezone"):
        pending().retry(datetime(2026, 10, 1), "x")


def test_delivery_needs_an_author_label() -> None:
    with pytest.raises(DomainError, match="label"):
        Delivery.of(MESSAGE, Target(MESH), " ", NOW)


def test_delivery_due_time_must_be_timezone_aware() -> None:
    with pytest.raises(DomainError, match="timezone"):
        Delivery.of(MESSAGE, Target(MESH), "NatAda", datetime(2026, 10, 1))


def test_attempts_cannot_be_negative() -> None:
    with pytest.raises(DomainError, match="negative"):
        Delivery(MessageId("m1"), MESH, "NatAda", "x", NOW, attempts=-1)
