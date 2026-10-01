"""Loading and hot-reloading `chatko.yaml` (docs/design.md §10, architecture.md §5)."""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from chatko.application.config import Config, ConfigError, ExtensionTypes, validate_config
from chatko.application.ports import AdminNotices, ConfigLoader
from chatko.application.routing import ReloadOutcome

_log = logging.getLogger("chatko.config")

CONFIG_NOTICE_KEY = "config:load"


class ConfigOutcome(StrEnum):
    """What `ConfigService.reload` did."""

    LOADED = "loaded"
    """A new valid config is `current`."""
    UNCHANGED = "unchanged"
    """The file says what it said at the last read: nothing to do."""
    REFUSED = "refused"
    """The file cannot be read or is not valid: `current` is the previous config, if any, and the
    admin was told."""


class ScriptReloader(Protocol):
    """Reloads the routing script: the `RoutingEngine`."""

    async def reload(self) -> ReloadOutcome: ...


@dataclass(frozen=True, slots=True)
class Refresh:
    """What `ConfigService.refresh` did; `routing` is `None` without a routing engine."""

    config: ConfigOutcome
    routing: ReloadOutcome | None


class ConfigService:
    """Keeps the last valid `Config`.

    `reload` reads the file, validates it and keeps the result if it is valid. A file that cannot
    be read or is not valid is refused: the previous config stays `current`, `errors` says why and
    the admin gets a notice (key `config:load`), once for each different problem. Reading the same
    content again, valid or not, changes and reports nothing. At start `current` stays `None` after
    a refusal, and the composition root decides what to do about it.

    `refresh` is what a watcher calls when the config or the routing script may have changed: it
    reloads the config and then the routing script.
    """

    def __init__(
        self,
        *,
        loader: ConfigLoader,
        types: ExtensionTypes,
        notices: AdminNotices,
        routing: ScriptReloader | None = None,
    ) -> None:
        self._loader = loader
        self._types = types
        self._notices = notices
        self._routing = routing
        self._current: Config | None = None
        self._errors: tuple[str, ...] = ()
        self._last_raw: dict[str, Any] | None = None
        self._reported: tuple[str, ...] | None = None

    @property
    def current(self) -> Config | None:
        """The last valid config; `None` until one was loaded."""
        return self._current

    @property
    def errors(self) -> tuple[str, ...]:
        """The problems of the latest read, empty if it was valid."""
        return self._errors

    async def reload(self) -> ConfigOutcome:
        try:
            raw = dict(await self._loader.load())
        except (OSError, ConfigError) as error:
            self._last_raw = None  # whatever comes next is news
            problems = error.errors if isinstance(error, ConfigError) else (str(error),)
            return await self._refuse(problems)
        if raw == self._last_raw:
            return ConfigOutcome.UNCHANGED
        self._last_raw = raw
        try:
            config = validate_config(raw, self._types)
        except ConfigError as error:
            return await self._refuse(error.errors)
        self._current, self._errors, self._reported = config, (), None
        _log.info("the config is loaded")
        return ConfigOutcome.LOADED

    async def refresh(self) -> Refresh:
        config = await self.reload()
        routing = None if self._routing is None else await self._routing.reload()
        return Refresh(config, routing)

    async def _refuse(self, problems: tuple[str, ...]) -> ConfigOutcome:
        self._errors = problems
        if problems != self._reported:
            self._reported = problems
            running = (
                "The previous config keeps running."
                if self._current is not None
                else "There is no valid config to run."
            )
            text = "The config was not loaded:\n" + "\n".join(f"- {p}" for p in problems)
            _log.error("%s", text)
            try:
                await self._notices.notify(f"{text}\n{running}", key=CONFIG_NOTICE_KEY)
            except Exception:
                _log.exception("an admin notice about the config was lost")
        return ConfigOutcome.REFUSED
