"""What a routing script must define (design.md §9.4, §9.5): `RoutingScript.of`."""

from types import SimpleNamespace

import pytest

from chatko.routing_api import (
    API_VERSION,
    RoutedMessage,
    RoutingContext,
    RoutingScript,
    ScriptError,
    Target,
    mirror,
)


def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    return mirror(msg, ctx)


def label(msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
    del msg, target, ctx
    return "X"


def test_a_script_needs_only_route() -> None:
    script = RoutingScript.of(SimpleNamespace(route=route))

    assert script == RoutingScript(route, None, API_VERSION)


def test_a_script_may_define_label_and_api_version() -> None:
    script = RoutingScript.of(SimpleNamespace(route=route, label=label, api_version=(1, 0)))

    assert script == RoutingScript(route, label, (1, 0))


def test_a_script_for_an_older_minor_version_is_taken() -> None:
    assert RoutingScript.of(SimpleNamespace(route=route, api_version=(1, 0))).api_version == (1, 0)


def test_a_script_without_route_is_refused() -> None:
    with pytest.raises(ScriptError, match=r"defines no route\(msg, ctx\)"):
        RoutingScript.of(SimpleNamespace(label=label))


def test_route_must_be_a_function() -> None:
    with pytest.raises(ScriptError, match="route is not a function: 'mirror'"):
        RoutingScript.of(SimpleNamespace(route="mirror"))


def test_route_must_take_a_message_and_a_context() -> None:
    def one_argument(msg: RoutedMessage) -> list[Target]:
        return []

    with pytest.raises(ScriptError, match=r"route must take \(msg, ctx\), not \(msg"):
        RoutingScript.of(SimpleNamespace(route=one_argument))


def test_label_must_take_a_message_a_target_and_a_context() -> None:
    # The common mistake: default_label(author, ctx) used as the hook.
    def by_author(author: object, ctx: object) -> str:
        return "X"

    with pytest.raises(ScriptError, match=r"label must take \(msg, target, ctx\)"):
        RoutingScript.of(SimpleNamespace(route=route, label=by_author))


def test_label_must_be_a_function() -> None:
    with pytest.raises(ScriptError, match="label is not a function"):
        RoutingScript.of(SimpleNamespace(route=route, label="NatAda"))


def test_functions_with_more_optional_arguments_are_taken() -> None:
    def flexible(*args: object, **kwargs: object) -> list[Target]:
        return []

    assert RoutingScript.of(SimpleNamespace(route=flexible)).route is flexible


def test_a_callable_without_a_readable_signature_is_trusted() -> None:
    builtin: object = min  # Its signature cannot be read.

    assert RoutingScript.of(SimpleNamespace(route=builtin)).route is builtin


@pytest.mark.parametrize(
    ("version", "problem"),
    [
        ((2, 0), "written for routing API 2.0, and this hub runs 1.0"),
        ((0, 9), "written for routing API 0.9"),
        ((1, 99), "written for routing API 1.99"),
    ],
)
def test_a_script_for_an_unsupported_version_is_refused(
    version: tuple[int, int], problem: str
) -> None:
    with pytest.raises(ScriptError, match=problem):
        RoutingScript.of(SimpleNamespace(route=route, api_version=version))


@pytest.mark.parametrize("version", ["1.0", (1,), (1, 0, 0), [1, 0], (1, "0"), (True, 0), 1])
def test_api_version_must_be_a_pair_of_numbers(version: object) -> None:
    with pytest.raises(ScriptError, match=r"api_version must be \(major, minor\)"):
        RoutingScript.of(SimpleNamespace(route=route, api_version=version))


def test_the_version_is_checked_before_the_functions() -> None:
    # A script for another major version may define other functions: say why it cannot run.
    with pytest.raises(ScriptError, match=r"routing API 2\.0"):
        RoutingScript.of(SimpleNamespace(api_version=(2, 0)))
