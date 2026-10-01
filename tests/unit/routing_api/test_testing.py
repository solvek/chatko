"""The test kit for routing scripts: `FakeInstallation`, `RouteResult`, `assert_routed_to`."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from chatko.routing_api import (
    AccountKey,
    AttachmentKind,
    EndpointRef,
    RoutedMessage,
    RoutingContext,
    ScriptError,
    Target,
    mirror,
    to_endpoint,
)
from chatko.routing_api.testing import DEFAULT_NOW, FakeInstallation, assert_routed_to


def installation() -> FakeInstallation:
    return FakeInstallation(
        extensions={"tg": "telegram", "kyiv": "meshtastic"},
        groups={"family": {"tg": "tg", "channel": "kyiv", "radio": "kyiv"}},
        sources={"longfast": "kyiv"},
        recipients={"family.radio": ["!a1", "!b2"]},
        people={"NatAda": ["telegram:111", "meshtastic:!a1"]},
    )


def mirrored(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    return mirror(msg, ctx)


MIRROR = SimpleNamespace(route=mirrored)


# The installation


def test_the_installation_is_described_as_in_the_config() -> None:
    hub = installation()
    ctx = hub.context()

    assert hub.endpoint("family.radio") == EndpointRef("kyiv", "family.radio")
    assert [site.name for site in ctx.group("family").sites] == [
        "family.tg",
        "family.channel",
        "family.radio",
    ]
    assert ctx.source("longfast") == EndpointRef("kyiv", "longfast")
    assert ctx.extension_type(hub.endpoint("family.tg")) == "telegram"
    assert ctx.recipients(hub.endpoint("family.radio")) == ("!a1", "!b2")
    assert ctx.person_of(AccountKey("meshtastic", "!a1")) is not None
    assert ctx.now == DEFAULT_NOW


def test_an_empty_installation_has_only_extensions() -> None:
    ctx = FakeInstallation(extensions={"tg": "telegram"}).context()

    assert ctx.groups == ()
    assert ctx.sources == {}
    assert ctx.people == ()


def test_an_endpoint_needs_a_listed_extension_instance() -> None:
    with pytest.raises(KeyError, match=r"family\.tg uses extension instance 'tg'"):
        FakeInstallation(extensions={}, groups={"family": {"tg": "tg"}})


def test_an_unknown_endpoint_is_a_key_error() -> None:
    with pytest.raises(KeyError, match=r"no endpoint 'family\.briar'"):
        installation().endpoint("family.briar")


def test_the_clock_can_be_moved() -> None:
    hub = installation()
    hub.now += timedelta(hours=1)

    assert hub.context().now == DEFAULT_NOW + timedelta(hours=1)
    assert hub.message("family.tg", "x").received_at == DEFAULT_NOW + timedelta(hours=1)


# Messages


def test_a_message_by_a_person() -> None:
    msg = installation().message(
        "family.tg", "Привіт", author="telegram:111", name="Наталія Адамчук"
    )

    assert msg.endpoint == EndpointRef("tg", "family.tg")
    assert msg.group is not None
    assert msg.group.name == "family"
    assert msg.text == "Привіт"
    assert msg.author.account.display_name == "Наталія Адамчук"
    assert msg.author.person is not None
    assert msg.author.person.label == "NatAda"


def test_by_default_a_message_is_by_a_stranger_of_the_endpoints_network() -> None:
    hub = installation()
    first, second = hub.message("family.radio", "a"), hub.message("longfast", "b")

    assert first.author.account.key.kind == "meshtastic"
    assert first.author.person is None
    assert first.group is not None
    assert second.group is None
    assert first.id != second.id
    assert first.author.account.key != second.author.account.key


def test_a_message_with_attachments_names_and_a_recipient() -> None:
    msg = installation().message(
        "family.radio",
        "caption",
        author="meshtastic:!b2",
        name="Base Camp",
        short_name="BC1",
        attachments=[AttachmentKind.PHOTO],
        from_recipient="!b2",
    )

    assert msg.plain_text == "[photo] caption"
    assert msg.author.account.short_name == "BC1"
    assert msg.from_recipient == "!b2"


def test_a_message_relayed_by_a_peer_has_the_peers_label_and_no_person() -> None:
    msg = installation().message("family.tg", "x", author="telegram:111", relayed_label="Ola")

    assert msg.author.relayed_label == "Ola"
    assert msg.author.person is None


# History


def test_heard_accounts_are_in_the_context() -> None:
    hub = installation()
    hub.hear("meshtastic:!a1", endpoint="family.channel", ago=timedelta(minutes=10))
    ctx = hub.context()

    node = AccountKey("meshtastic", "!a1")
    assert ctx.last_heard(node, hub.endpoint("family.channel")) == DEFAULT_NOW - timedelta(
        minutes=10
    )
    assert ctx.last_heard(node) == DEFAULT_NOW - timedelta(minutes=10)
    assert ctx.last_heard(node, hub.endpoint("longfast")) is None


def test_hearing_anywhere_is_now_by_default() -> None:
    hub = installation()
    hub.hear("telegram:111")

    assert hub.context().last_heard(AccountKey("telegram", "111")) == DEFAULT_NOW


def test_seen_messages_are_in_the_context() -> None:
    hub = installation()
    earlier = hub.message("family.tg", "same", author="telegram:111")
    hub.see(earlier, ago=timedelta(hours=2))
    hub.see(hub.message("longfast", "other").fingerprint)
    msg = hub.message("family.radio", "same", author="meshtastic:!a1")
    ctx = hub.context()

    assert ctx.seen(msg.fingerprint, within=timedelta(hours=3))
    assert not ctx.seen(msg.fingerprint, within=timedelta(hours=1))


# Routing with a script


def test_route_labels_each_target_with_the_default_label() -> None:
    hub = installation()
    result = hub.route(MIRROR, hub.message("family.tg", "Привіт", author="telegram:111"))

    assert result.endpoints == ["family.channel", "family.radio"]
    assert len(result) == 2
    assert [out.label for out in result] == ["NatAda", "NatAda"]
    out = result.to("family.radio")
    assert out.text == "Привіт"
    assert out.recipients == ("!a1", "!b2")
    assert out.formatted == "NatAda: Привіт"
    assert out.target == Target(hub.endpoint("family.radio"))
    assert result.to("family.channel").recipients == ()
    assert repr(result) == "RouteResult(['family.channel', 'family.radio'])"


def test_route_uses_the_scripts_label_hook() -> None:
    def label(msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        return f"{msg.author.account.display_name}@{target.endpoint.name}"

    hub = installation()
    msg = hub.message("family.tg", "x", name="Ada")
    result = hub.route(SimpleNamespace(route=mirrored, label=label), msg)

    assert result.to("family.channel").label == "Ada@family.channel"


def test_a_targets_own_text_label_and_recipients_win() -> None:
    def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
        radio = ctx.endpoint("family.radio")
        return [to_endpoint(radio, text="short", label="Nat", recipients=["!b2", "!zz"])]

    def label(msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        raise AssertionError("not called for a target with its own label")

    hub = installation()
    msg = hub.message("family.tg", "long text", attachments=[AttachmentKind.PHOTO])
    out = hub.route(SimpleNamespace(route=route, label=label), msg).to("family.radio")

    assert (out.label, out.text, out.recipients) == ("Nat", "short", ("!b2",))
    assert out.formatted == "Nat: [photo] short"


def test_route_shows_what_the_script_asked_for_before_the_invariants() -> None:
    def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
        return [to_endpoint(msg.endpoint), to_endpoint(msg.endpoint, label="Again")]

    hub = installation()
    result = hub.route(SimpleNamespace(route=route), hub.message("family.tg", "x"))

    assert result.endpoints == ["family.tg", "family.tg"]
    assert result.to("family.tg").label != "Again"


def test_route_checks_the_script_as_the_hub_does() -> None:
    hub = installation()

    script = SimpleNamespace(route=mirrored, api_version=(2, 0))

    with pytest.raises(ScriptError, match=r"routing API 2\.0"):
        hub.route(script, hub.message("family.tg", "x"))


def test_route_raises_the_scripts_errors() -> None:
    def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
        return [to_endpoint(ctx.endpoint("family.briar"))]

    hub = installation()

    with pytest.raises(KeyError, match=r"family\.briar"):
        hub.route(SimpleNamespace(route=route), hub.message("family.tg", "x"))


def test_to_an_endpoint_without_a_target_is_a_key_error() -> None:
    hub = installation()
    result = hub.route(MIRROR, hub.message("longfast", "x"))

    with pytest.raises(KeyError, match=r"no target at 'family.tg'; the targets are \[\]"):
        result.to("family.tg")


# Assertions


def test_assert_routed_to_ignores_the_order() -> None:
    hub = installation()
    result = hub.route(MIRROR, hub.message("family.tg", "x"))

    assert_routed_to(result, "family.radio", "family.channel")


def test_assert_routed_to_nowhere() -> None:
    hub = installation()

    assert_routed_to(hub.route(MIRROR, hub.message("longfast", "x")))


def test_assert_routed_to_names_the_missing_and_the_unexpected_endpoints() -> None:
    hub = installation()
    result = hub.route(MIRROR, hub.message("family.tg", "x"))

    with pytest.raises(AssertionError, match=r"^routed not to longfast; also to family.radio: "):
        assert_routed_to(result, "family.channel", "longfast")
    with pytest.raises(AssertionError, match=r"^routed also to family.channel, family.radio"):
        assert_routed_to(result)
    with pytest.raises(AssertionError, match=r"^routed not to longfast: "):
        assert_routed_to(result, "family.channel", "family.radio", "longfast")
