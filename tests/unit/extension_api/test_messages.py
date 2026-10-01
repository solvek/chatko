from datetime import UTC, datetime, timedelta

import pytest

from chatko.extension_api import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Delivered,
    DeliveryResult,
    EndpointRef,
    Failed,
    InboundMessage,
    MessageId,
    OutboundMessage,
    Retry,
)

TG = EndpointRef("tg", "family.tg")
ADA = Account(AccountKey("telegram", "1"), "Ada Lovelace")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PHOTO = Attachment(AttachmentKind.PHOTO)


def outbound(text: str = "Привіт", attachments: tuple[Attachment, ...] = ()) -> OutboundMessage:
    return OutboundMessage(MessageId("m1"), "NatAda", text, NOW, attachments)


def test_inbound_message_carries_what_the_extension_read() -> None:
    message = InboundMessage(TG, "42", ADA, "Привіт")

    assert message.endpoint == TG
    assert message.transport_id == "42"
    assert message.author == ADA
    assert message.text == "Привіт"
    assert message.attachments == ()


def test_inbound_message_may_be_an_attachment_without_text() -> None:
    assert InboundMessage(TG, "42", ADA, "", (PHOTO,)).attachments == (PHOTO,)


def test_inbound_message_needs_text_or_an_attachment() -> None:
    with pytest.raises(ValueError, match="empty"):
        InboundMessage(TG, "42", ADA, "  ")


def test_inbound_message_needs_a_transport_id() -> None:
    with pytest.raises(ValueError, match="transport id"):
        InboundMessage(TG, "", ADA, "Привіт")


def test_outbound_message_is_a_first_attempt_to_the_whole_endpoint_by_default() -> None:
    message = outbound()

    assert message.attempt == 1
    assert message.recipient is None


def test_outbound_formatted_is_the_label_and_the_text() -> None:
    assert outbound().formatted == "NatAda: Привіт"


def test_outbound_formatted_puts_placeholders_before_the_caption() -> None:
    message = outbound("на річці", (PHOTO,))

    assert message.plain_text == "[photo] на річці"
    assert message.formatted == "NatAda: [photo] на річці"


def test_outbound_message_needs_an_author_label() -> None:
    with pytest.raises(ValueError, match="label"):
        OutboundMessage(MessageId("m1"), " ", "Привіт", NOW)


def test_outbound_message_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        OutboundMessage(MessageId("m1"), "NatAda", "Привіт", datetime(2026, 10, 1))


def test_outbound_attempts_count_from_one() -> None:
    with pytest.raises(ValueError, match="from 1"):
        OutboundMessage(MessageId("m1"), "NatAda", "Привіт", NOW, attempt=0)


def test_retry_waits_for_the_hubs_backoff_unless_told() -> None:
    assert Retry("busy").after is None
    assert Retry("flood wait", after=timedelta(seconds=30)).after == timedelta(seconds=30)


def test_retry_cannot_be_due_in_the_past() -> None:
    with pytest.raises(ValueError, match="past"):
        Retry("busy", after=timedelta(seconds=-1))


@pytest.mark.parametrize(
    ("result", "outcome"),
    [(Delivered(), "delivered"), (Retry("busy"), "retry"), (Failed("gone"), "failed")],
)
def test_delivery_results_can_be_matched(result: DeliveryResult, outcome: str) -> None:
    match result:
        case Delivered():
            matched = "delivered"
        case Retry():
            matched = "retry"
        case Failed():
            matched = "failed"

    assert matched == outcome


def test_inbound_message_may_name_the_recipient_that_posted_it() -> None:
    assert InboundMessage(TG, "1", ADA, "x").from_recipient is None
    assert InboundMessage(TG, "1", ADA, "x", from_recipient="!a1").from_recipient == "!a1"


def test_inbound_message_recipient_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="empty recipient"):
        InboundMessage(TG, "1", ADA, "x", from_recipient="")
