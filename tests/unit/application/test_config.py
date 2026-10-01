import copy
from datetime import timedelta
from typing import Any

import pytest

from chatko.application.config import ConfigError, _InertHub, validate_config
from chatko.domain import Account, AccountKey, EndpointRef
from chatko.extension_api import Extension, HubContext, InboundMessage
from chatko.extension_api.testing import FakeConfig, FakeEndpointConfig, FakeExtension

TYPES = {"fake": FakeExtension}

RAW: dict[str, Any] = {
    "extensions": {"tg": {"type": "fake"}, "mesh": {"type": "fake", "account": "mesh"}},
    "groups": {
        "family": {
            "sites": {
                "tg": {"ext": "tg", "place": "family"},
                "radio": {"ext": "mesh", "place": "dm", "recipients": ["!a1", "!b2"]},
            }
        },
        "street": {"sites": {"tg": {"ext": "tg", "place": "street"}}},
    },
    "sources": {"owner": {"ext": "tg", "place": "owner"}},
    "admin_notices": {"to": "owner", "new_accounts": True},
    "people": {"NatAda": ["fake:nat", "fake:nat2"]},
    "fingerprint_dedup_s": {"family.radio": 3600},
    "routing": "routing.py",
    "retention_days": 3,
    "peers": {"home": ["fake:home"]},
}


def problems(raw: dict[str, Any]) -> tuple[str, ...]:
    with pytest.raises(ConfigError) as error:
        validate_config(raw, TYPES)
    return error.value.errors


def changed(**sections: Any) -> dict[str, Any]:
    raw = copy.deepcopy(RAW)
    for name, value in sections.items():
        if value is None:
            del raw[name]
        else:
            raw[name] = value
    return raw


def test_a_valid_config_becomes_the_topology_and_the_settings() -> None:
    config = validate_config(RAW, TYPES)

    assert list(config.extensions) == ["tg", "mesh"]
    assert config.topology.endpoint("family.radio") == EndpointRef("mesh", "family.radio")
    assert [site.name for site in config.topology.group("family").sites] == [
        "family.tg",
        "family.radio",
    ]
    assert config.topology.source("owner") == EndpointRef("tg", "owner")
    assert config.topology.people[0].label == "NatAda"
    assert config.admin_endpoint == EndpointRef("tg", "owner")
    assert config.new_accounts
    assert config.fingerprint_dedup == {EndpointRef("mesh", "family.radio"): timedelta(hours=1)}
    assert config.routing == "routing.py"
    assert config.retention == timedelta(days=3)
    assert config.peers == {"home": frozenset({AccountKey("fake", "home")})}


def test_endpoints_are_validated_by_their_extension_and_kept_per_instance_in_order() -> None:
    config = validate_config(RAW, TYPES)

    assert [e.name for e in config.endpoints["tg"]] == ["family.tg", "street.tg", "owner"]
    radio = config.endpoints["mesh"][EndpointRef("mesh", "family.radio")]
    assert isinstance(radio, FakeEndpointConfig)
    assert radio.recipients == ("!a1", "!b2")
    mesh = config.extensions["mesh"].config
    assert isinstance(mesh, FakeConfig)
    assert mesh.account == "mesh"


def test_an_empty_config_is_valid_and_has_the_defaults() -> None:
    config = validate_config({}, TYPES)

    assert config.topology.endpoints == frozenset()
    assert config.admin_endpoint is None
    assert not config.new_accounts
    assert config.routing is None
    assert config.retention == timedelta(days=7)


def test_an_instance_without_endpoints_is_listed() -> None:
    config = validate_config({"extensions": {"tg": {"type": "fake"}}}, TYPES)

    assert config.endpoints == {"tg": {}}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (changed(unknown=1) | {"unknown": 1}, "unknown: Extra inputs are not permitted"),
        (changed(retention_days=0), "retention_days: Input should be greater than or equal to 1"),
        (changed(routing=""), "routing: String should have at least 1 character"),
        (
            changed(extensions={"tg": {"type": "nope"}}, groups={}, sources={}, admin_notices=None),
            "extensions.tg: unknown extension type 'nope'; installed: fake",
        ),
        (
            changed(extensions={"tg": {"account": "x"}}, groups={}, sources={}, admin_notices=None),
            "extensions.tg: `type` is required and names the extension",
        ),
        (
            changed(
                extensions={"tg": {"type": "fake", "color": "red"}},
                groups={},
                sources={},
                admin_notices=None,
            ),
            "extensions.tg.color: Extra inputs are not permitted",
        ),
        (
            changed(groups={"g": {"sites": {"a": {"place": "x"}}}}),
            "groups.g.sites.a: `ext` is required and names an extension instance",
        ),
        (
            changed(groups={"g": {"sites": {"a": {"ext": "nope", "place": "x"}}}}),
            "groups.g.sites.a: `ext` 'nope' is not an extension instance of the config",
        ),
        (
            changed(groups={"g": {"sites": {"a": {"ext": "tg"}}}}),
            "groups.g.sites.a.place: Field required",
        ),
        (
            changed(groups={"g": {"sites": {"a": {"ext": "tg", "place": "x", "chat": 1}}}}),
            "groups.g.sites.a.chat: Extra inputs are not permitted",
        ),
        (
            changed(groups={"g": {"sites": {}}}),
            "groups.g.sites: Dictionary should have at least 1 item after validation, not 0",
        ),
    ],
)
def test_invalid_config_lists_the_problem_with_its_place(
    raw: dict[str, Any], expected: str
) -> None:
    assert expected in problems(raw)


def test_two_endpoints_that_the_extension_takes_for_one_place_are_refused_by_the_extension() -> (
    None
):
    raw = changed(sources={"owner": {"ext": "tg", "place": "family"}})

    [error] = problems(raw)

    assert error.startswith("extensions.tg: ValueError: ")
    assert "same place 'family'" in error


def test_a_site_and_a_source_cannot_share_a_name() -> None:
    raw = changed(sources={"family.tg": {"ext": "tg", "place": "elsewhere"}})

    [error] = problems(raw)

    assert "share a name" in error or "also a site" in error


def test_problems_of_the_sections_are_reported_together() -> None:
    raw = changed(people={"A": ["bad"], "B": []}, peers={"home": ["worse"]}, retention_days=0)

    assert problems(raw)[0].startswith("retention_days: ")
    raw = changed(people={"A": ["bad"], "B": []}, peers={"home": ["worse"]})

    errors = problems(raw)

    assert len(errors) == 3
    assert any(e.startswith("people.A: ") for e in errors)
    assert any(e.startswith("people.B: ") for e in errors)
    assert any(e.startswith("peers.home: ") for e in errors)


def test_what_names_an_endpoint_is_checked_against_the_endpoints() -> None:
    raw = changed(
        admin_notices={"to": "nowhere"}, fingerprint_dedup_s={"nowhere": 5, "family.tg": -1}
    )

    errors = problems(raw)

    assert len(errors) == 3
    assert any(e.startswith("admin_notices.to: no site or source 'nowhere'") for e in errors)
    assert any(e.startswith("fingerprint_dedup_s.nowhere: no site or source") for e in errors)
    assert "fingerprint_dedup_s.family.tg: the window must be positive" in errors


def test_the_admin_endpoint_may_be_a_site() -> None:
    config = validate_config(changed(admin_notices={"to": "family.tg"}), TYPES)

    assert config.admin_endpoint == EndpointRef("tg", "family.tg")


def test_an_account_of_two_people_is_refused() -> None:
    raw = changed(people={"A": ["fake:x"], "B": ["fake:x"]})

    [error] = problems(raw)

    assert "fake:x" in error


def test_a_peer_account_must_be_written_as_kind_and_id() -> None:
    assert problems(changed(peers={"home": ["nonsense"]})) == (
        "peers.home: an account is written as kind:id, got 'nonsense'",
    )


def test_an_extension_without_endpoints_cannot_have_any() -> None:
    class NoEndpoints(Extension[Any]):
        type_name = "plain"
        api_version = (1, 0)
        config_model = FakeExtension.config_model

        async def start(self) -> None: ...

        async def stop(self) -> None: ...

    raw = {
        "extensions": {"p": {"type": "plain"}},
        "sources": {"s": {"ext": "p"}},
    }
    with pytest.raises(ConfigError) as error:
        validate_config(raw, {"plain": NoEndpoints})
    assert error.value.errors == ("sources.s: extension 'p' (plain) has no endpoints",)


def test_the_errors_never_contain_the_values_of_the_config() -> None:
    raw = changed(extensions={"tg": {"type": "fake", "account": 12345, "max_text": "s3cr3t"}})

    text = str(ConfigError(problems(raw)))

    assert "s3cr3t" not in text
    assert "extensions.tg.max_text" in text


def test_the_extension_is_constructed_on_a_fresh_instance_and_may_refuse() -> None:
    class Picky(FakeExtension):
        type_name = "fake"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            raise ValueError("no way")

    with pytest.raises(ConfigError) as error:
        validate_config(RAW, {"fake": Picky})

    assert "extensions.tg: ValueError: no way" in error.value.errors


async def test_an_extension_that_uses_the_hub_before_start_is_refused() -> None:
    class Eager(FakeExtension):
        type_name = "fake"

        def __init__(self, instance: str, config: FakeConfig, hub: HubContext) -> None:
            super().__init__(instance, config, hub)
            hub.now()

    with pytest.raises(ConfigError) as error:
        validate_config(RAW, {"fake": Eager})

    assert "RuntimeError: an extension must not use the hub before `start`" in error.value.errors[0]


async def test_the_hub_of_a_checked_instance_refuses_every_call() -> None:
    hub = _InertHub()
    calls = [
        hub.submit(InboundMessage(EndpointRef("a", "b"), "1", Account(AccountKey("k", "1")), "x")),
        hub.heard(Account(AccountKey("k", "1"))),
        hub.retry_now(EndpointRef("a", "b")),
        hub.notify_admin("x"),
    ]
    for call in calls:
        with pytest.raises(RuntimeError):
            await call
    with pytest.raises(RuntimeError):
        hub.now()
