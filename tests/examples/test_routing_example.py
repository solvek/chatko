"""Tests of `routing.example.py`, written as an admin tests their own `config/routing.py`.

To test your script, copy this file to `config/test_routing.py`, replace the lines that load the
example with `import routing`, describe your installation in `installation()` as in `chatko.yaml`,
and run `pytest` in `config/`.
"""

import importlib.util
from pathlib import Path

from chatko.application.routing import load_script
from chatko.routing_api import RoutingScript
from chatko.routing_api.testing import FakeInstallation, assert_routed_to

EXAMPLE = Path(__file__).parents[2] / "routing.example.py"
_spec = importlib.util.spec_from_file_location("routing", EXAMPLE)
assert _spec is not None
assert _spec.loader is not None
routing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routing)

NAT_TG = "telegram:111111111"
NAT_RADIO = "meshtastic:!a1b2c3d4"


def installation() -> FakeInstallation:
    """The installation of `config.example.yaml`."""
    return FakeInstallation(
        extensions={"telegram": "telegram", "kyiv": "meshtastic", "briar": "briar"},
        groups={
            "family": {
                "telegram": "telegram",
                "briar": "briar",
                "radio": "kyiv",
            },
            "street": {"telegram": "telegram", "briar": "briar", "radio": "kyiv"},
        },
        sources={"longfast": "kyiv", "owner": "telegram"},
        recipients={
            "family.radio": ["!a1b2c3d4", "!0badc0de"],
            "street.radio": ["!a1b2c3d4"],
        },
        people={"NatAda": [NAT_TG, NAT_RADIO, "briar:nat"]},
    )


def test_the_script_is_written_for_this_routing_api() -> None:
    assert RoutingScript.of(routing).api_version == (1, 0)


def test_the_hub_loads_the_script() -> None:
    script = load_script(EXAMPLE.read_text(encoding="utf-8"), str(EXAMPLE))

    assert script.label is not None


def test_a_message_goes_to_the_other_sites_of_its_group() -> None:
    hub = installation()
    msg = hub.message("family.telegram", "Привіт", author=NAT_TG, name="Наталія Адамчук")

    result = hub.route(routing, msg)

    assert_routed_to(result, "family.briar", "family.radio")
    assert {out.label for out in result} == {"NatAda"}
    assert result.to("family.radio").recipients == ("!a1b2c3d4", "!0badc0de")


def test_people_not_in_the_config_are_marked() -> None:
    hub = installation()
    msg = hub.message("family.briar", "Привіт", author="briar:x", name="Сергій Петренко")

    assert hub.route(routing, msg).to("family.telegram").formatted == "~SerPet: Привіт"


def test_longfast_goes_to_the_owner_signed_with_the_nodes_names() -> None:
    hub = installation()
    msg = hub.message(
        "longfast", "Всім привіт", author="meshtastic:!0badf00d", name="Base Camp", short_name="BC1"
    )

    result = hub.route(routing, msg)

    assert_routed_to(result, "owner")
    assert result.to("owner").formatted == "[BC1] Base Camp: Всім привіт"


def test_a_node_without_a_short_name_on_longfast_gets_a_marked_default_label() -> None:
    hub = installation()
    msg = hub.message("longfast", "test", author="meshtastic:!0badf00d", name="Base Camp")

    assert hub.route(routing, msg).to("owner").label == "~BasCam"


def test_a_tagged_family_message_also_goes_to_the_street_without_the_tag() -> None:
    hub = installation()
    msg = hub.message("family.telegram", "#street Збори о 18:00", author=NAT_TG)

    result = hub.route(routing, msg)

    assert_routed_to(
        result,
        "family.briar",
        "family.radio",
        "street.telegram",
        "street.briar",
        "street.radio",
    )
    assert result.to("family.briar").text == "#street Збори о 18:00"
    assert result.to("street.telegram").formatted == "NatAda: Збори о 18:00"


def test_a_direct_message_from_one_radio_reaches_the_other() -> None:
    hub = installation()
    msg = hub.message("family.radio", "Я тут", author=NAT_RADIO, from_recipient="!a1b2c3d4")

    result = hub.route(routing, msg)

    assert_routed_to(result, "family.telegram", "family.briar", "family.radio")
    assert result.to("family.radio").recipients == ("!0badc0de",)


def test_a_message_with_a_no_mirror_tag_is_not_relayed() -> None:
    hub = installation()
    for text in ("#nomirror тут", "тут #no-mirror", "тут #NM, а далі", "(#nm)"):
        msg = hub.message("family.telegram", text, author=NAT_TG)

        assert list(hub.route(routing, msg)) == []


def test_a_word_that_only_starts_like_a_no_mirror_tag_is_relayed() -> None:
    hub = installation()
    msg = hub.message("family.telegram", "#nmap і #nomirrors", author=NAT_TG)

    assert_routed_to(hub.route(routing, msg), "family.briar", "family.radio")
