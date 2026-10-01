"""What a routing script must define, checked when it is loaded (docs/design.md §9.4, §9.5)."""

import inspect
from dataclasses import dataclass
from typing import Any, Self, cast

from chatko.routing_api.helpers import LabelFunction, RouteFunction
from chatko.routing_api.version import API_VERSION, is_supported


class ScriptError(Exception):
    """A routing script the hub cannot run. The message says why, for the admin."""


@dataclass(frozen=True, slots=True)
class RoutingScript:
    """A routing script's functions, checked: `route`, the optional `label` hook, and the version
    of this API the script was written for."""

    route: RouteFunction
    label: LabelFunction | None = None
    api_version: tuple[int, int] = API_VERSION

    @classmethod
    def of(cls, script: object) -> Self:
        """The functions a loaded script defines: a module, or any object with the same
        attributes.

        Raises `ScriptError` unless the script defines `route(msg, ctx)`, a `label(msg, target,
        ctx)` that takes those arguments if it defines one, and an `api_version` this API
        supports if it declares one. A script without `api_version` is taken to be current.
        """
        version = _api_version(getattr(script, "api_version", API_VERSION))
        route = getattr(script, "route", None)
        if route is None:
            raise ScriptError("it defines no route(msg, ctx) function")
        label = getattr(script, "label", None)
        return cls(
            _function(route, "route", 2, "msg, ctx"),
            None if label is None else _function(label, "label", 3, "msg, target, ctx"),
            version,
        )


def _api_version(value: object) -> tuple[int, int]:
    if not (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, int) and not isinstance(part, bool) for part in value)
    ):
        raise ScriptError(f"api_version must be (major, minor), like (1, 0), not {value!r}")
    version = cast("tuple[int, int]", value)
    if not is_supported(version):
        raise ScriptError(
            f"it is written for routing API {version[0]}.{version[1]}, and this hub runs "
            f"{API_VERSION[0]}.{API_VERSION[1]}"
        )
    return version


def _function(value: object, name: str, arguments: int, signature: str) -> Any:  # noqa: ANN401
    if not callable(value):
        raise ScriptError(f"{name} is not a function: {value!r}")
    try:
        parameters = inspect.signature(value)
    except (TypeError, ValueError):  # Some callables have no signature to read: trust them.
        return value
    try:
        parameters.bind(*[None] * arguments)
    except TypeError:
        raise ScriptError(f"{name} must take ({signature}), not {parameters}") from None
    return value
