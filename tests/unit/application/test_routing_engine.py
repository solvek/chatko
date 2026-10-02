"""The routing engine: the routing script, its reloads and its errors (design.md §9.4, §9.5)."""

import re
import sys
from textwrap import dedent

import pytest

from chatko.application.routing import ReloadOutcome, RoutingEngine, load_script
from chatko.application.testing import FakeClock, InMemoryScriptSource, RecordingNotices
from chatko.domain import Author, EndpointRef, Message, MessageId, Target
from chatko.routing_api import API_VERSION, RoutedMessage, RoutingContext, ScriptError
from tests.unit.application.rig import (
    ADA,
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    NAT,
    NATADA,
    OWNER,
    PLACES,
    STREET_TG,
    TOPOLOGY,
    Rig,
    settle,
)

NOW = FakeClock().now()
CTX = RoutingContext(TOPOLOGY, now=NOW)
MIRRORED = [Target(FAMILY_RADIO), Target(FAMILY_CHANNEL)]

TO_OWNER = """\
from chatko.routing_api import to_endpoint

def route(msg, ctx):
    return [to_endpoint(ctx.source("owner"))]
"""

SIGNED = """\
from chatko.routing_api import mirror

def route(msg, ctx):
    return mirror(msg, ctx)

def label(msg, target, ctx):
    return "~" + msg.author.account.display_name[:3]
"""


def routed(text: str = "Привіт", endpoint: EndpointRef = FAMILY_TG) -> RoutedMessage:
    message = Message(MessageId("m1"), endpoint, "t1", Author(NAT, NATADA), text, NOW)
    return RoutedMessage(message, TOPOLOGY.group_of(endpoint))


def script(body: str) -> str:
    return dedent(body)


class Engine:
    """An engine over an in-memory script and recorded notices."""

    def __init__(self, code: str | None = None) -> None:
        self.source = InMemoryScriptSource(code)
        self.notices = RecordingNotices()
        self.engine = RoutingEngine(source=self.source, notices=self.notices)

    async def load(self, code: str | None) -> ReloadOutcome:
        self.source.code = code
        return await self.reload()

    async def reload(self) -> ReloadOutcome:
        """Reload, and let the notices that the engine posts in tasks arrive."""
        outcome = await self.engine.reload()
        await settle()
        return outcome

    @property
    def texts(self) -> list[str]:
        return [notice.text for notice in self.notices.notices]


# load_script


def test_a_script_is_loaded_from_its_code() -> None:
    loaded = load_script(SIGNED, "routing.py")

    assert loaded.route(routed(), CTX) == MIRRORED
    assert loaded.label is not None
    assert loaded.label(routed(), Target(FAMILY_RADIO), CTX) == "~Нат"
    assert loaded.api_version == API_VERSION


def test_a_script_that_does_not_compile_is_refused_with_its_line() -> None:
    with pytest.raises(ScriptError, match="line 2: "):
        load_script("def route(msg, ctx):\nreturn []\n", "routing.py")


def test_a_script_that_fails_while_loading_is_refused_with_its_line() -> None:
    code = "from chatko.routing_api import mirror\n\nLATELY = timedelta(hours=1)\n"

    with pytest.raises(ScriptError, match="line 3: NameError: name 'timedelta' is not defined"):
        load_script(code, "routing.py")


def test_an_error_in_code_the_script_calls_is_refused_with_the_scripts_line() -> None:
    message = "line 1: ModuleNotFoundError: No module named 'nowhere'"

    with pytest.raises(ScriptError, match=f"^{re.escape(message)}$"):
        load_script("import nowhere\n", "routing.py")


def test_a_script_that_exits_while_loading_is_refused() -> None:
    with pytest.raises(ScriptError, match=r"^line 2: SystemExit: 3$"):
        load_script("import sys\nsys.exit(3)\n", "routing.py")


def test_code_with_a_null_byte_is_refused() -> None:
    with pytest.raises(ScriptError, match=r"^source code string cannot contain null bytes$"):
        load_script("route = 1\0\n", "routing.py")


def test_a_script_that_defines_no_route_is_refused() -> None:
    with pytest.raises(ScriptError, match=r"defines no route\(msg, ctx\)"):
        load_script("x = 1\n", "routing.py")


def test_a_script_may_use_dataclasses_and_postponed_annotations() -> None:
    code = script(
        """\
        from __future__ import annotations
        from dataclasses import dataclass, KW_ONLY

        @dataclass
        class Rule:
            _: KW_ONLY
            to: str

        RULE = Rule(to="owner")

        def route(msg, ctx):
            return []
        """
    )

    assert load_script(code, "routing.py").route(routed(), CTX) == []
    assert "chatko_routing_script" not in sys.modules


# Reloads


async def test_without_a_script_the_defaults_run() -> None:
    hub = Engine()

    assert await hub.reload() is ReloadOutcome.DEFAULTS
    assert hub.engine.script is None
    assert hub.engine.route(routed(), CTX) == MIRRORED
    assert hub.engine.label(routed(), Target(FAMILY_RADIO), CTX) == "NatAda"
    assert hub.texts == []


async def test_a_loaded_script_routes_and_labels() -> None:
    hub = Engine()

    assert await hub.load(SIGNED) is ReloadOutcome.LOADED
    assert hub.engine.route(routed(), CTX) == MIRRORED
    assert hub.engine.label(routed(), Target(FAMILY_RADIO), CTX) == "~Нат"


async def test_without_a_label_hook_the_default_label_is_used() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)

    assert hub.engine.route(routed(), CTX) == [Target(OWNER)]
    assert hub.engine.label(routed(), Target(OWNER), CTX) == "NatAda"


async def test_the_same_code_again_changes_nothing() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)
    running = hub.engine.script

    assert await hub.load(TO_OWNER) is ReloadOutcome.UNCHANGED
    assert hub.engine.script is running


async def test_no_script_again_changes_nothing() -> None:
    hub = Engine()
    await hub.load(None)

    assert await hub.load(None) is ReloadOutcome.UNCHANGED


async def test_a_new_version_replaces_the_running_one() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)

    assert await hub.load(SIGNED) is ReloadOutcome.LOADED
    assert hub.engine.route(routed(), CTX) == MIRRORED


async def test_a_broken_new_version_keeps_the_previous_one_running() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)

    assert await hub.load("def route(msg, ctx)\n") is ReloadOutcome.REFUSED

    assert hub.engine.route(routed(), CTX) == [Target(OWNER)]
    assert hub.texts == [
        "The routing script routing.py was not loaded: line 1: expected ':'. "
        "The previous version keeps running."
    ]
    assert hub.notices.notices[0].key == "routing:load"


async def test_a_broken_script_at_start_leaves_the_defaults_running() -> None:
    hub = Engine()

    assert await hub.load("x = 1\n") is ReloadOutcome.REFUSED

    assert hub.engine.script is None
    assert hub.engine.route(routed(), CTX) == MIRRORED
    assert hub.texts == [
        "The routing script routing.py was not loaded: it defines no route(msg, ctx) function. "
        "The default routing runs."
    ]


async def test_a_script_for_an_unsupported_api_version_runs_the_defaults_and_says_why() -> None:
    hub = Engine()

    assert await hub.load(TO_OWNER + "api_version = (2, 0)\n") is ReloadOutcome.REFUSED

    assert hub.engine.route(routed(), CTX) == MIRRORED
    assert hub.texts == [
        "The routing script routing.py was not loaded: it is written for routing API 2.0, and "
        "this hub runs 1.0. The default routing runs."
    ]


async def test_a_refused_script_is_reported_once() -> None:
    hub = Engine()
    await hub.load("x = 1\n")

    assert await hub.load("x = 1\n") is ReloadOutcome.UNCHANGED
    assert len(hub.texts) == 1


async def test_a_removed_script_leaves_the_defaults_running() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)

    assert await hub.load(None) is ReloadOutcome.DEFAULTS
    assert hub.engine.script is None
    assert hub.engine.route(routed(), CTX) == MIRRORED


async def test_a_script_that_cannot_be_read_keeps_the_previous_one_running() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)
    hub.source.error = PermissionError(13, "Permission denied", "routing.py")

    assert await hub.reload() is ReloadOutcome.REFUSED

    assert hub.engine.route(routed(), CTX) == [Target(OWNER)]
    assert hub.texts == [
        "The routing script routing.py was not loaded: it cannot be read: [Errno 13] Permission "
        "denied: 'routing.py'. The previous version keeps running."
    ]


async def test_after_a_read_error_the_script_is_loaded_again() -> None:
    hub = Engine()
    await hub.load(TO_OWNER)
    hub.source.error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    await hub.reload()
    hub.source.error = None

    assert await hub.reload() is ReloadOutcome.LOADED


async def test_a_lost_load_notice_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    hub = Engine()
    hub.notices.notify = broken_notify  # type: ignore[method-assign]

    assert await hub.load("x = 1\n") is ReloadOutcome.REFUSED
    assert "an admin notice about the routing script was lost" in caplog.text


async def broken_notify(text: str, *, key: str) -> None:
    raise RuntimeError("no admin endpoint")


# Errors while routing


RAISES = """\
from chatko.routing_api import mirror

def route(msg, ctx):
    if msg.text == "boom":
        return [ctx.source("nowhere")]
    if msg.text == "zero":
        return 1 / 0
    return mirror(msg, ctx)

def label(msg, target, ctx):
    if msg.text == "label boom":
        raise RuntimeError("no label")
    if msg.text == "empty":
        return " "
    if msg.text == "number":
        return 7
    return "OK"
"""


async def test_when_route_raises_the_message_goes_by_the_defaults() -> None:
    hub = Engine()
    await hub.load(RAISES)

    assert hub.engine.route(routed("boom"), CTX) == MIRRORED
    await settle()

    assert hub.texts == [
        "The routing script's route() at line 5 failed: KeyError: \"no source 'nowhere'\". A "
        "message from family.telegram got the default routing, and so will others with this error "
        "until the script changes; they are not reported again."
    ]
    assert hub.notices.notices[0].key == "routing:route:KeyError:5"


async def test_each_kind_of_error_is_reported_once() -> None:
    hub = Engine()
    await hub.load(RAISES)

    for text in ["boom", "boom", "zero", "zero", "fine", "boom"]:
        hub.engine.route(routed(text), CTX)
    await settle()

    assert [notice.key for notice in hub.notices.notices] == [
        "routing:route:KeyError:5",
        "routing:route:ZeroDivisionError:7",
    ]


async def test_errors_are_reported_again_after_the_script_changes() -> None:
    hub = Engine()
    await hub.load(RAISES)
    hub.engine.route(routed("boom"), CTX)
    await hub.load(RAISES + "\n")
    hub.engine.route(routed("boom"), CTX)
    await settle()

    assert len(hub.texts) == 2


@pytest.mark.parametrize(
    ("returned", "error"),
    [
        ("None", "TypeError: route() returned None, not a list of targets"),
        ("to_endpoint(ctx.source('owner'))", "route() returned Target("),
        ("'owner'", "route() returned 'owner', not a list of targets"),
        ("['owner']", "route() returned 'owner' among its targets, not a Target"),
    ],
)
async def test_route_must_return_targets(returned: str, error: str) -> None:
    hub = Engine()
    await hub.load(
        "from chatko.routing_api import to_endpoint\n\n"
        f"def route(msg, ctx):\n    return {returned}\n"
    )

    assert hub.engine.route(routed(), CTX) == MIRRORED
    await settle()
    assert error in hub.texts[0]


async def test_route_may_return_any_iterable_of_targets() -> None:
    hub = Engine()
    await hub.load(
        "from chatko.routing_api import mirror\n\n"
        "def route(msg, ctx):\n    yield from mirror(msg, ctx)\n"
    )

    assert hub.engine.route(routed(), CTX) == MIRRORED


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("label boom", "label() at line 12 failed: RuntimeError: no label"),
        ("empty", "label() failed: ValueError: label() returned an empty label: ' '"),
        ("number", "label() failed: TypeError: label() returned 7, not a str"),
    ],
)
async def test_when_label_fails_the_default_label_is_used(text: str, error: str) -> None:
    hub = Engine()
    await hub.load(RAISES)

    assert hub.engine.label(routed(text), Target(FAMILY_RADIO), CTX) == "NatAda"
    await settle()

    assert error in hub.texts[0]
    assert "got the default label" in hub.texts[0]


async def test_a_script_that_exits_while_routing_falls_back_on_the_defaults() -> None:
    hub = Engine()
    await hub.load(
        "import sys\n\ndef route(msg, ctx):\n    sys.exit()\n\n"
        "def label(msg, target, ctx):\n    raise SystemExit('bye')\n"
    )

    assert hub.engine.route(routed(), CTX) == MIRRORED
    assert hub.engine.label(routed(), Target(FAMILY_RADIO), CTX) == "NatAda"
    await settle()

    assert [notice.key for notice in hub.notices.notices] == [
        "routing:route:SystemExit:4",
        "routing:label:SystemExit:7",
    ]


async def test_a_lost_error_notice_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    hub = Engine()
    await hub.load(RAISES)
    hub.notices.notify = broken_notify  # type: ignore[method-assign]

    hub.engine.route(routed("boom"), CTX)
    await settle()

    assert "an admin notice about the routing script was lost" in caplog.text


# Through the hub: a script's targets reach the networks (§9.2, §9.4, §9.5)


THROUGH_THE_HUB = """\
from chatko.routing_api import mirror, to_endpoint

def route(msg, ctx):
    if msg.text == "boom":
        raise RuntimeError("a bug")
    if msg.text.startswith("#street "):
        street = ctx.endpoint("street.telegram")
        return mirror(msg, ctx) + [
            to_endpoint(street, text=msg.text.removeprefix("#street "), label="Family")
        ]
    radio = ctx.endpoint("family.radio")
    return [to_endpoint(radio, recipients=["!b2"])] + mirror(msg, ctx)

def label(msg, target, ctx):
    return "~" + msg.author.account.display_name.split()[0]
"""


async def hub_with_script() -> tuple[Rig, Engine]:
    engine = Engine(THROUGH_THE_HUB)
    await engine.engine.reload()
    rig = Rig(router=engine.engine)
    await rig.start()
    return rig, engine


async def test_targets_with_their_own_text_and_label_reach_the_networks() -> None:
    rig, _ = await hub_with_script()

    rig.networks["telegram"].post(PLACES[FAMILY_TG].place, ADA, "#street Збори о 18:00")
    await settle()

    assert rig.posted(STREET_TG) == ["Family: Збори о 18:00"]
    assert rig.posted(FAMILY_CHANNEL) == ["~Ada: #street Збори о 18:00"]
    await rig.stop()


async def test_a_target_narrowed_to_some_recipients_reaches_only_them() -> None:
    # The first target that includes a recipient wins (§9.3): mirror's target for all nodes
    # comes second and adds nothing for !b2, but reaches !a1.
    rig, _ = await hub_with_script()

    rig.networks["telegram"].post(PLACES[FAMILY_TG].place, ADA, "Привіт")
    await settle()

    assert rig.posted(FAMILY_RADIO, recipient="!b2") == ["~Ada: Привіт"]
    assert rig.posted(FAMILY_RADIO, recipient="!a1") == ["~Ada: Привіт"]
    await rig.stop()


async def test_a_message_the_script_fails_on_goes_by_the_defaults() -> None:
    rig, engine = await hub_with_script()

    rig.networks["telegram"].post(PLACES[FAMILY_TG].place, ADA, "boom")
    await settle()

    assert rig.posted(FAMILY_CHANNEL) == ["~Ada: boom"]
    assert rig.posted(FAMILY_RADIO, recipient="!a1") == ["~Ada: boom"]
    assert [notice.key for notice in engine.notices.notices] == ["routing:route:RuntimeError:5"]
    await rig.stop()
