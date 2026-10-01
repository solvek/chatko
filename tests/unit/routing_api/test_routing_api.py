from datetime import UTC, datetime, timedelta

import pytest

from chatko.domain import Message, Topology
from chatko.routing_api import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Author,
    EndpointRef,
    Group,
    MessageId,
    Person,
    RoutedMessage,
    RoutingContext,
    Target,
    default_label,
    mirror,
    to_endpoint,
)
from chatko.routing_api.testing import FakeHistory

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FAMILY_TG = EndpointRef("tg", "family.tg")
FAMILY_BRIAR = EndpointRef("briar", "family.briar")
FAMILY_RADIO = EndpointRef("kyiv", "family.radio")
STREET_TG = EndpointRef("tg", "street.tg")
LONGFAST = EndpointRef("kyiv", "longfast")
FAMILY = Group("family", (FAMILY_TG, FAMILY_BRIAR, FAMILY_RADIO))
STREET = Group("street", (STREET_TG,))
NAT_TG = AccountKey("telegram", "111")
NATA = Person("NatAda", frozenset({NAT_TG}))
NODE = AccountKey("meshtastic", "!a1b2c3d4")
TOPOLOGY = Topology(groups=(FAMILY, STREET), sources={"longfast": LONGFAST}, people=(NATA,))


def context(history: FakeHistory | None = None) -> RoutingContext:
    return RoutingContext(
        TOPOLOGY,
        now=NOW,
        extension_types={"tg": "telegram", "briar": "briar", "kyiv": "meshtastic"},
        recipients={FAMILY_RADIO: ("!a1b2c3d4", "!0badc0de")},
        history=history,
    )


def message(endpoint: EndpointRef = FAMILY_TG, text: str = "Привіт") -> Message:
    author = TOPOLOGY.author_of(Account(NAT_TG, "Наталія Адамчук"))
    attachments = (Attachment(AttachmentKind.PHOTO),)
    return Message(MessageId("m1"), endpoint, "42", author, text, NOW, attachments)


def routed(endpoint: EndpointRef = FAMILY_TG) -> RoutedMessage:
    return RoutedMessage(message(endpoint), TOPOLOGY.group_of(endpoint))


# RoutedMessage


def test_routed_message_shows_the_stored_message() -> None:
    stored = message()
    msg = RoutedMessage(stored, FAMILY)

    assert msg.id == "m1"
    assert msg.endpoint == FAMILY_TG
    assert msg.group == FAMILY
    assert msg.author == stored.author
    assert msg.text == "Привіт"
    assert msg.attachments == (Attachment(AttachmentKind.PHOTO),)
    assert msg.plain_text == "[photo] Привіт"
    assert msg.fingerprint == stored.fingerprint
    assert msg.received_at == NOW
    assert repr(msg) == "RoutedMessage('m1' from tg/family.tg)"


def test_routed_message_from_a_source_has_no_group() -> None:
    assert routed(LONGFAST).group is None


def test_routed_message_group_must_be_its_endpoints() -> None:
    with pytest.raises(ValueError, match="not a site of group 'street'"):
        RoutedMessage(message(FAMILY_TG), STREET)


# RoutingContext


def test_context_shows_the_installation() -> None:
    ctx = context()

    assert ctx.now == NOW
    assert ctx.groups == (FAMILY, STREET)
    assert ctx.sources == {"longfast": LONGFAST}
    assert ctx.people == (NATA,)
    assert ctx.group("street") == STREET
    assert ctx.group_of(FAMILY_RADIO) == FAMILY
    assert ctx.group_of(LONGFAST) is None
    assert ctx.source("longfast") == LONGFAST
    assert ctx.endpoint("family.radio") == FAMILY_RADIO
    assert ctx.person_of(NAT_TG) == NATA
    assert ctx.person_of(NODE) is None


def test_context_knows_the_extension_type_of_an_endpoint() -> None:
    assert context().extension_type(FAMILY_RADIO) == "meshtastic"


def test_extension_type_of_an_unknown_instance_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="no extension instance 'x'"):
        context().extension_type(EndpointRef("x", "y"))


def test_context_knows_the_recipients_of_an_endpoint() -> None:
    ctx = context()

    assert ctx.recipients(FAMILY_RADIO) == ("!a1b2c3d4", "!0badc0de")
    assert ctx.recipients(FAMILY_TG) == ()


def test_context_needs_a_timezone_aware_clock() -> None:
    with pytest.raises(ValueError, match="timezone"):
        RoutingContext(TOPOLOGY, now=datetime(2026, 10, 1))


def test_without_history_nothing_was_heard_or_seen() -> None:
    ctx = RoutingContext(TOPOLOGY, now=NOW)

    assert ctx.last_heard(NODE) is None
    assert not ctx.seen(routed().fingerprint, within=timedelta(days=1))


def test_last_heard_at_an_endpoint_or_anywhere() -> None:
    history = FakeHistory()
    history.hear(NODE, NOW - timedelta(hours=2), FAMILY_RADIO)
    history.hear(NODE, NOW - timedelta(minutes=5), LONGFAST)
    ctx = context(history)

    assert ctx.last_heard(NODE, FAMILY_RADIO) == NOW - timedelta(hours=2)
    assert ctx.last_heard(NODE) == NOW - timedelta(minutes=5)
    assert ctx.last_heard(NODE, FAMILY_TG) is None
    assert ctx.last_heard(NAT_TG) is None


def test_seen_looks_back_as_far_as_asked() -> None:
    history = FakeHistory()
    fingerprint = routed().fingerprint
    history.see(fingerprint, NOW - timedelta(hours=2))
    ctx = context(history)

    assert ctx.seen(fingerprint, within=timedelta(hours=3))
    assert not ctx.seen(fingerprint, within=timedelta(hours=1))


def test_seen_needs_a_positive_time_span() -> None:
    with pytest.raises(ValueError, match="positive"):
        context().seen(routed().fingerprint, within=timedelta(0))


# Helpers


def test_to_endpoint_is_a_plain_target() -> None:
    assert to_endpoint(FAMILY_TG) == Target(FAMILY_TG)


def test_to_endpoint_with_overrides() -> None:
    target = to_endpoint(FAMILY_RADIO, text="#street hi", label="Nata", recipients=["!a1b2c3d4"])

    assert target == Target(FAMILY_RADIO, "#street hi", "Nata", frozenset({"!a1b2c3d4"}))


def test_to_endpoint_with_an_empty_list_of_recipients_reaches_none() -> None:
    assert to_endpoint(FAMILY_RADIO, recipients=[]).recipients == frozenset()


def test_mirror_sends_to_all_other_sites_of_the_group_in_config_order() -> None:
    assert mirror(routed(FAMILY_BRIAR), context()) == [Target(FAMILY_TG), Target(FAMILY_RADIO)]


def test_mirror_sends_a_source_nowhere() -> None:
    assert mirror(routed(LONGFAST), context()) == []


def test_default_label_is_the_domain_default() -> None:
    nata = routed().author
    stranger = Author(Account(AccountKey("telegram", "222"), "Сергій Петренко"))

    assert default_label(nata, context()) == "NatAda"
    assert default_label(stranger, context()) == "SerPet"


def dm(from_recipient: str | None) -> RoutedMessage:
    base = message(FAMILY_RADIO)
    msg = Message(base.id, FAMILY_RADIO, "7", base.author, "x", NOW, from_recipient=from_recipient)
    return RoutedMessage(msg, FAMILY)


def test_a_routed_message_names_the_recipient_that_posted_it() -> None:
    assert dm("!a1b2c3d4").from_recipient == "!a1b2c3d4"
    assert routed().from_recipient is None


def test_mirror_sends_a_recipients_message_to_the_sites_other_recipients() -> None:
    assert mirror(dm("!a1b2c3d4"), context()) == [
        Target(FAMILY_TG),
        Target(FAMILY_BRIAR),
        to_endpoint(FAMILY_RADIO, recipients=["!0badc0de"]),
    ]


def test_mirror_keeps_away_from_a_site_with_recipients_if_the_sender_is_unknown() -> None:
    assert mirror(dm(None), context()) == [Target(FAMILY_TG), Target(FAMILY_BRIAR)]


def test_mirror_skips_a_site_whose_only_recipient_posted() -> None:
    ctx = RoutingContext(TOPOLOGY, now=NOW, recipients={FAMILY_RADIO: ("!a1b2c3d4",)})

    assert mirror(dm("!a1b2c3d4"), ctx) == [Target(FAMILY_TG), Target(FAMILY_BRIAR)]
