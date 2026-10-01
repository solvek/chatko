"""The router the pipeline calls: the routing script, or the defaults (docs/design.md §9.4)."""

import asyncio
import logging
import sys
import traceback
import types
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Protocol

from chatko.application.ports import AdminNotices, RoutingScriptSource
from chatko.domain import Target
from chatko.routing_api import (
    RoutedMessage,
    RoutingContext,
    RoutingScript,
    ScriptError,
    default_label,
    mirror,
)

_log = logging.getLogger("chatko.routing")

_MODULE = "chatko_routing_script"
"""The module name the script runs under, so that it can define dataclasses and the like."""

LOAD_NOTICE_KEY = "routing:load"

_SCRIPT_ERRORS = (Exception, SystemExit)
"""What a script's errors are: a stray `exit()` in it is a bug of the script, not a stop."""


class Router(Protocol):
    """Decides where a message goes and how its author is signed. Never raises: the routing
    engine handles a script's errors by falling back to the defaults."""

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]: ...

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str: ...


class DefaultRouter:
    """The defaults: `mirror` and `default_label`, as without a routing script."""

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]:
        return mirror(msg, ctx)

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        del target
        return default_label(msg.author, ctx)


def load_script(code: str, origin: str) -> RoutingScript:
    """Run a routing script's code as a module of its own and check what it defines.

    Raises `ScriptError` when the code does not compile, raises while it runs, or does not define
    what a routing script must (`RoutingScript.of`). `origin` is the script's name in tracebacks
    and errors: its path.
    """
    try:
        compiled = compile(code, origin, "exec", dont_inherit=True)
    except SyntaxError as error:
        where = "" if error.lineno is None else f"line {error.lineno}: "
        raise ScriptError(f"{where}{error.msg}") from error
    module = types.ModuleType(_MODULE)
    module.__file__ = origin
    sys.modules[_MODULE] = module  # Only while it runs: what it defines keeps its globals.
    try:
        exec(compiled, module.__dict__)  # noqa: S102 - the admin's script, trusted like the config
    except _SCRIPT_ERRORS as error:
        raise ScriptError(_describe(error, origin)) from error
    finally:
        sys.modules.pop(_MODULE, None)
    return RoutingScript.of(module)


class ReloadOutcome(StrEnum):
    """What `RoutingEngine.reload` did."""

    LOADED = "loaded"
    """A new version of the script runs."""
    DEFAULTS = "defaults"
    """There is no script (any more): the defaults run."""
    REFUSED = "refused"
    """The script could not be read or loaded: the previous version, or the defaults, keep
    running, and the admin was told."""
    UNCHANGED = "unchanged"
    """The code is the same as last time: nothing changed."""


class RoutingEngine:
    """The router of a running hub: the admin's routing script, or the defaults without one.

    `reload` reads the script and runs it if it changed. A script that cannot be read or loaded,
    or is written for a routing API this hub does not support, is refused: the previous version
    keeps running (the defaults if there is none), and the admin is told.

    Each function falls back on its own default: when `route` raises or returns something that
    is not a list of targets, the message goes by `mirror`; when `label` raises or returns no
    label, that target gets `default_label`. The error is logged, and the admin is told once per
    kind of error (the function, the exception type and the line of the script it came from)
    until the script changes. Notices are posted in tasks of their own, so routing never waits
    for them (an admin notifier may itself go through the pipeline, which is busy routing), and
    a refusal during a config reload goes to the admin of the snapshot that the reload publishes
    in the same step as the script's swap.
    """

    def __init__(self, *, source: RoutingScriptSource, notices: AdminNotices) -> None:
        self._source = source
        self._notices = notices
        self._defaults = DefaultRouter()
        self._script: RoutingScript | None = None
        self._code: str | None = None
        self._read = False
        self._reported: set[tuple[str, str, int | None]] = set()
        self._notices_posting: set[asyncio.Task[None]] = set()

    @property
    def script(self) -> RoutingScript | None:
        """The script that runs now; `None` while the defaults run."""
        return self._script

    async def reload(self) -> ReloadOutcome:
        """Read the script and run its new version if it changed (design.md §9.4)."""
        origin = self._source.origin
        try:
            code = await self._source.read()
        except (OSError, UnicodeDecodeError) as error:
            self._read = False  # Whatever comes next is news.
            return self._refuse(f"it cannot be read: {error}")
        if self._read and code == self._code:
            return ReloadOutcome.UNCHANGED
        self._read, self._code = True, code
        if code is None:
            if self._script is not None:
                _log.info("the routing script %s is gone: the default routing runs", origin)
            self._use(None)
            return ReloadOutcome.DEFAULTS
        try:
            script = load_script(code, origin)
        except ScriptError as error:
            return self._refuse(str(error))
        self._use(script)
        _log.info("the routing script %s is loaded", origin)
        return ReloadOutcome.LOADED

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]:
        script = self._script
        if script is None:
            return self._defaults.route(msg, ctx)
        try:
            return _targets(script.route(msg, ctx))
        except _SCRIPT_ERRORS as error:
            self._failed("route", msg, error, "the default routing")
            return self._defaults.route(msg, ctx)

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        script = self._script
        if script is None or script.label is None:
            return self._defaults.label(msg, target, ctx)
        try:
            return _label(script.label(msg, target, ctx))
        except _SCRIPT_ERRORS as error:
            self._failed("label", msg, error, "the default label")
            return self._defaults.label(msg, target, ctx)

    def _use(self, script: RoutingScript | None) -> None:
        self._script = script
        self._reported.clear()

    def _refuse(self, reason: str) -> ReloadOutcome:
        running = (
            "The previous version keeps running"
            if self._script is not None
            else "The default routing runs"
        )
        text = f"The routing script {self._source.origin} was not loaded: {reason}. {running}."
        _log.error("%s", text)
        self._post(text, LOAD_NOTICE_KEY)
        return ReloadOutcome.REFUSED

    def _failed(
        self, function: str, msg: RoutedMessage, error: BaseException, fallback: str
    ) -> None:
        line = _line_of(error, self._source.origin)
        kind = (function, type(error).__qualname__, line)
        where = "" if line is None else f" at line {line}"
        if kind in self._reported:
            _log.warning(
                "the routing script's %s()%s failed again for message %s: %s: %s",
                function,
                where,
                msg.id,
                type(error).__name__,
                error,
            )
            return
        self._reported.add(kind)
        _log.error(
            "the routing script's %s()%s failed for message %s from %s",
            function,
            where,
            msg.id,
            msg.endpoint,
            exc_info=error,
        )
        self._post(
            f"The routing script's {function}(){where} failed: {type(error).__name__}: "
            f"{error}. A message from {msg.endpoint.name} got {fallback}, and so will others "
            "with this error until the script changes; they are not reported again.",
            f"routing:{function}:{kind[1]}:{line}",
        )

    def _post(self, text: str, key: str) -> None:
        """Post an admin notice in a task of its own, without waiting for it."""
        task = asyncio.get_running_loop().create_task(self._notify(text, key))
        self._notices_posting.add(task)
        task.add_done_callback(self._notices_posting.discard)

    async def _notify(self, text: str, key: str) -> None:
        try:
            await self._notices.notify(text, key=key)
        except Exception:
            _log.exception("an admin notice about the routing script was lost: %s", text)


def _targets(result: object) -> list[Target]:
    if isinstance(result, Target | str | bytes) or not isinstance(result, Iterable):
        raise TypeError(f"route() returned {result!r}, not a list of targets")
    targets = list(result)
    for target in targets:
        if not isinstance(target, Target):
            raise TypeError(f"route() returned {target!r} among its targets, not a Target")
    return targets


def _label(result: object) -> str:
    if not isinstance(result, str):
        raise TypeError(f"label() returned {result!r}, not a str")
    if not result.strip():
        raise ValueError(f"label() returned an empty label: {result!r}")
    return result


def _line_of(error: BaseException, origin: str) -> int | None:
    """The line of the script where the error came from: its innermost frame in the script."""
    lines = [
        frame.lineno
        for frame in traceback.extract_tb(error.__traceback__)
        if frame.filename == origin
    ]
    return lines[-1] if lines else None


def _describe(error: BaseException, origin: str) -> str:
    # Raised while the script's module ran, so the traceback has a frame in the script.
    return f"line {_line_of(error, origin)}: {type(error).__name__}: {error}"
