from datetime import UTC, datetime

import pytest

from chatko.domain import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Author,
    DomainError,
    EndpointRef,
    Message,
    MessageId,
    Person,
    Target,
    fingerprint,
)

TG = EndpointRef("telegram", "chat:-100")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
NATA = Account(AccountKey("telegram", "111"), "Наталія Адамчук")


def message(
    text: str = "Привіт",
    attachments: tuple[Attachment, ...] = (),
    author: Author | None = None,
) -> Message:
    return Message(
        MessageId("m1"), TG, "42", author or Author(NATA), text, NOW, attachments=attachments
    )


def test_attachment_placeholder() -> None:
    assert Attachment(AttachmentKind.PHOTO).placeholder == "[photo]"


def test_plain_text_of_a_text_message_is_its_text() -> None:
    assert message().plain_text == "Привіт"


def test_plain_text_puts_placeholders_before_the_caption() -> None:
    photos = (Attachment(AttachmentKind.PHOTO), Attachment(AttachmentKind.LOCATION))

    assert message("caption", photos).plain_text == "[photo] [location] caption"


def test_plain_text_of_an_attachment_without_caption_is_the_placeholder() -> None:
    assert message("", (Attachment(AttachmentKind.VOICE),)).plain_text == "[voice]"


def test_fingerprint_uses_the_default_label_and_the_plain_text() -> None:
    msg = message("caption", (Attachment(AttachmentKind.PHOTO),))

    assert msg.fingerprint == fingerprint("NatAda", "[photo] caption")


def test_fingerprint_of_a_person_uses_their_label() -> None:
    person = Person("Nata", frozenset({NATA.key}))

    assert message(author=Author(NATA, person)).fingerprint == fingerprint("Nata", "Привіт")


def test_a_peer_relay_matches_the_original() -> None:
    # The home hub relays "NatAda: Привіт" over the mesh; the original comes later through Briar.
    peer = Account(AccountKey("meshtastic", "!0badf00d"), "home")
    relayed = message(author=Author(peer, relayed_label="NatAda"))

    assert relayed.fingerprint == message().fingerprint


def test_message_needs_text_or_an_attachment() -> None:
    with pytest.raises(DomainError, match="empty"):
        message("  ")


def test_message_needs_an_id_and_a_transport_id() -> None:
    with pytest.raises(DomainError, match="needs an id"):
        Message(MessageId(""), TG, "42", Author(NATA), "x", NOW)
    with pytest.raises(DomainError, match="transport id"):
        Message(MessageId("m1"), TG, "", Author(NATA), "x", NOW)


def test_message_time_must_be_timezone_aware() -> None:
    with pytest.raises(DomainError, match="timezone"):
        Message(MessageId("m1"), TG, "42", Author(NATA), "x", datetime(2026, 10, 1))


def test_target_overrides_are_optional() -> None:
    target = Target(TG)

    assert target.text is None
    assert target.label is None
    assert target.recipients is None


def test_target_without_recipients_includes_every_recipient() -> None:
    assert Target(TG).includes("!a1b2c3d4")


def test_target_with_recipients_includes_only_those() -> None:
    target = Target(TG, recipients=frozenset({"!a1b2c3d4"}))

    assert target.includes("!a1b2c3d4")
    assert not target.includes("!0badc0de")


def test_target_with_no_recipients_includes_nobody() -> None:
    assert not Target(TG, recipients=frozenset()).includes("!a1b2c3d4")


def test_target_label_cannot_be_empty() -> None:
    with pytest.raises(DomainError, match="label"):
        Target(TG, label=" ")


def test_a_message_names_the_recipient_that_posted_it_if_any() -> None:
    author = Author(NATA)
    assert Message(MessageId("m1"), TG, "1", author, "x", NOW).from_recipient is None
    assert Message(MessageId("m1"), TG, "1", author, "x", NOW, (), "!a1").from_recipient == "!a1"


def test_the_recipient_that_posted_cannot_be_empty() -> None:
    with pytest.raises(DomainError, match="empty recipient"):
        Message(MessageId("m1"), TG, "1", Author(NATA), "x", NOW, (), " ")
