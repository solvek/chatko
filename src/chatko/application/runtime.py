"""The running hub: the services over the ports, the extension instances' lifecycle and the
config reloads (docs/architecture.md §3.1, §5.5)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from chatko.application.accounts import NewAccounts
from chatko.application.config import Config, ExtensionSetup, ExtensionTypes
from chatko.application.config_service import ConfigOutcome, ConfigService
from chatko.application.history import HistoryPersistence, HubHistory
from chatko.application.hub import ExtensionHub
from chatko.application.installation import Installation
from chatko.application.notifier import AdminNotifier
from chatko.application.outbox import OutboxSettings, OutboxWorker
from chatko.application.pipeline import InboundPipeline
from chatko.application.ports import (
    AccountRegistry,
    Clock,
    ConfigLoader,
    HistoryRepository,
    IdGenerator,
    MessageRepository,
    OutboxRepository,
    RoutingScriptSource,
)
from chatko.application.routing import ReloadOutcome, RoutingEngine
from chatko.domain import EndpointRef, Topology
from chatko.extension_api import EndpointProvider, Extension

_log = logging.getLogger("chatko.runtime")


@dataclass(frozen=True, slots=True)
class HubPorts:
    """Everything the hub needs from outside (architecture.md §5.1)."""

    clock: Clock
    ids: IdGenerator
    messages: MessageRepository
    outbox: OutboxRepository
    history: HistoryRepository
    accounts: AccountRegistry
    config: ConfigLoader
    script: RoutingScriptSource
    """The routing script the current config names."""


@dataclass(frozen=True, slots=True)
class HubSettings:
    """The outbox's numbers, how often the history is saved and old state pruned, and how far
    apart notices with the same key may come."""

    outbox: OutboxSettings = field(default_factory=OutboxSettings)
    flush_every: timedelta = timedelta(seconds=10)
    prune_every: timedelta = timedelta(days=1)
    notice_interval: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        if min(self.flush_every, self.prune_every) <= timedelta(0):
            raise ValueError("the flush and prune periods must be positive")


@dataclass(frozen=True, slots=True)
class Refresh:
    """What `HubRuntime.refresh` did; `routing` is `None` when the hub does not run."""

    config: ConfigOutcome
    routing: ReloadOutcome | None


class StartError(Exception):
    """The hub cannot start: there is no valid config. `errors` says why, a line per problem."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("no valid config:\n" + "\n".join(f"- {error}" for error in self.errors))


class HubRuntime:
    """The hub: it wires the application's services over the ports and runs the extension
    instances of the config.

    `start` loads the config (a `StartError` without a valid one), restores the routing history,
    constructs every instance and gives it its endpoints, loads the routing script, starts the
    instances one by one, and then the outbox worker, which loads the pending deliveries
    (including those of messages submitted meanwhile). Whenever an instance has started, the
    deliveries to it that wait for a retry are due at once. An instance whose `start` raises is
    not running: the admin is told, its deliveries wait, and it is tried again with the next
    config that loads. `refresh` is what the config watcher calls: it reloads `chatko.yaml` and
    the routing script and applies what changed (below). `stop` stops the worker first (the
    deliveries in progress end or are cut after its grace), then the instances, then waits for
    the submissions in progress and saves the history. Messages submitted while the worker is
    stopped are stored and delivered after the next start.

    Applying a new config: an instance that is gone, whose own section changed or that did not
    start is taken out of service (no new deliveries), its deliveries in progress are let end,
    and it is stopped; the routing script is reloaded; a running instance whose endpoints
    changed is given the new set; the new and changed instances are constructed; the new
    topology is published, in the same step as the script's swap; then the new instances start.
    Deliveries to an endpoint that is no longer in the config fail.

    The services read the current `Installation` per message and per delivery, so a reload only
    swaps the snapshot. The history is saved every `flush_every`, and old messages and arrivals
    are pruned at start and every `prune_every`.
    """

    def __init__(
        self, *, ports: HubPorts, types: ExtensionTypes, settings: HubSettings | None = None
    ) -> None:
        self._ports = ports
        self._types = types
        self._settings = HubSettings() if settings is None else settings
        self._history = HubHistory()
        self._persistence = HistoryPersistence(self._history, ports.history)
        self._installation = Installation(Topology())
        self._worker = OutboxWorker(
            installation=self.installation,
            messages=ports.messages,
            outbox=ports.outbox,
            clock=ports.clock,
            settings=self._settings.outbox,
        )
        self._notifier = AdminNotifier(
            installation=self.installation,
            messages=ports.messages,
            outbox=self._worker,
            clock=ports.clock,
            ids=ports.ids,
            min_interval=self._settings.notice_interval,
        )
        self._routing = RoutingEngine(source=ports.script, notices=self._notifier)
        self._pipeline = InboundPipeline(
            installation=self.installation,
            messages=ports.messages,
            outbox=self._worker,
            router=self._routing,
            history=self._history,
            clock=ports.clock,
            ids=ports.ids,
        )
        self._config = ConfigService(loader=ports.config, types=types, notices=self._notifier)
        self._accounts = NewAccounts(
            registry=ports.accounts,
            notices=self._notifier,
            clock=ports.clock,
            topology=lambda: self._installation.topology,
            enabled=lambda: self._config.current is not None and self._config.current.new_accounts,
        )
        self._endpoints: dict[str, list[tuple[EndpointRef, BaseModel]]] = {}
        """The endpoints each instance was given, in order."""
        self._instances: dict[str, Extension[Any]] = {}
        self._setups: dict[str, ExtensionSetup] = {}
        self._running: set[str] = set()
        self._lock = asyncio.Lock()
        self._started = False
        self._timers: list[asyncio.Task[None]] = []

    @property
    def config(self) -> Config | None:
        """The latest valid config: the one the hub runs with or is applying; `None` before
        `start`. The routing script it names is the one to load."""
        return self._config.current

    @property
    def extensions(self) -> Mapping[str, Extension[Any]]:
        """The extension instances by name, running or not."""
        return MappingProxyType(self._instances)

    @property
    def routing(self) -> RoutingEngine:
        return self._routing

    def installation(self) -> Installation:
        """The current snapshot: an `InstallationSource`."""
        return self._installation

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                raise RuntimeError("the hub is running already")
            await self._config.reload()
            config = self._config.current
            if config is None:
                raise StartError(self._config.errors)
            self._started = True
            try:
                self._history.retention = config.retention
                await self._persistence.load(self._ports.clock.now())
                _, starting = await self._apply(config)  # the script too, before the starts (D39)
                await self._start(starting)
                await self._worker.start()  # after them, so it calls only started instances
                for name in self._running:
                    self._wake(name)
            except BaseException:
                await self._stop()
                raise
            self._timers = [
                asyncio.create_task(self._every(self._settings.flush_every, self._flush, False)),
                asyncio.create_task(self._every(self._settings.prune_every, self._prune, True)),
            ]
            _log.info("the hub runs: %s", ", ".join(self._running) or "no extension instances")

    async def refresh(self) -> Refresh:
        """Reload the config and the routing script, and apply what changed."""
        async with self._lock:
            if not self._started:
                return Refresh(ConfigOutcome.UNCHANGED, None)
            outcome = await self._config.reload()
            new = self._config.current if outcome is ConfigOutcome.LOADED else None
            routing, starting = await self._apply(new)
            await self._start(starting)
            return Refresh(outcome, routing)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for timer in self._timers:
            timer.cancel()
        await asyncio.gather(*self._timers, return_exceptions=True)
        self._timers = []
        await self._worker.stop()
        for name in reversed(list(self._instances)):
            await self._stop_instance(name)
        await self._pipeline.drain()
        await self._flush()
        _log.info("the hub stopped")

    async def _apply(self, config: Config | None) -> tuple[ReloadOutcome, list[str]]:
        """Reload the routing script and apply a new `config`, if any, up to the start of the
        new instances, whose names it returns. The new script and the new topology take effect
        in the same step of the event loop, so no message is routed by one without the other."""
        if config is None:
            return await self._routing.reload(), []
        for name, setup in list(self._setups.items()):
            if config.extensions.get(name) != setup or name not in self._running:
                await self._stop_instance(name)
                del self._instances[name], self._setups[name], self._endpoints[name]
        routing = await self._routing.reload()
        # From the script's swap to the snapshot nothing awaits.
        problems: list[tuple[str, str]] = []
        for name, instance in self._instances.items():
            endpoints = config.endpoints[name]
            if list(endpoints.items()) != self._endpoints[name]:
                try:
                    _set_endpoints(instance, endpoints)
                    self._endpoints[name] = list(endpoints.items())
                except Exception as error:
                    _log.exception("%s did not take its new endpoints", name)
                    problems.append((name, f"did not take its new endpoints: {_describe(error)}"))
        starting = []
        for name, setup in config.extensions.items():
            if name in self._instances:
                continue
            try:
                instance = self._types[setup.type_name](name, setup.config, self._hub(name))
                _set_endpoints(instance, config.endpoints[name])
            except Exception as error:
                _log.exception("%s could not be created", name)
                problems.append((name, f"could not be created: {_describe(error)}"))
                continue
            self._instances[name], self._setups[name] = instance, setup
            self._endpoints[name] = list(config.endpoints[name].items())
            starting.append(name)
        self._history.retention = config.retention
        self._publish(config)
        for name, problem in problems:
            await self._notifier.notify(
                f"The extension instance {name} {problem}", key=f"extension:{name}"
            )
        return routing, starting

    async def _start(self, names: Sequence[str]) -> None:
        for name in names:
            try:
                await self._instances[name].start()
            except Exception as error:
                _log.exception("%s did not start", name)
                await self._notifier.notify(
                    f"The extension instance {name} did not start: {_describe(error)}. Messages "
                    "to its endpoints wait; it is tried again when the config changes.",
                    key=f"extension:{name}",
                )
                continue
            self._running.add(name)
            self._publish()
            _log.info("%s started", name)
            self._wake(name)

    def _wake(self, name: str) -> None:
        """Make the deliveries to a started instance that wait for a retry due now."""
        for endpoint, _ in self._endpoints[name]:
            self._worker.retry_now(endpoint)

    async def _stop_instance(self, name: str) -> None:
        self._running.discard(name)
        self._publish()
        await self._worker.finish_attempts(name)
        try:
            await self._instances[name].stop()
        except Exception:
            _log.exception("%s raised while stopping", name)

    def _publish(self, config: Config | None = None) -> None:
        """A new snapshot: the instances as they are now, and the topology of `config`, or of
        the current snapshot."""
        current = self._installation
        self._installation = Installation(
            current.topology if config is None else config.topology,
            self._instances,
            current.fingerprint_dedup if config is None else config.fingerprint_dedup,
            running=self._running & self._instances.keys(),
            admin_endpoint=current.admin_endpoint if config is None else config.admin_endpoint,
        )

    def _hub(self, name: str) -> ExtensionHub:
        return ExtensionHub(
            name,
            pipeline=self._pipeline,
            outbox=self._worker,
            history=self._history,
            notices=self._notifier,
            clock=self._ports.clock,
            accounts=self._accounts,
        )

    async def _every(
        self, period: timedelta, action: Callable[[], Awaitable[None]], at_once: bool
    ) -> None:
        clock = self._ports.clock
        if at_once:
            await action()
        while True:
            await clock.sleep_until(clock.now() + period)
            await action()

    async def _flush(self) -> None:
        try:
            await self._persistence.flush()
        except Exception:
            _log.exception("saving the routing history failed; it is tried again")

    async def _prune(self) -> None:
        now = self._ports.clock.now()
        try:
            pruned = await self._ports.messages.prune(now - self._history.retention)
            await self._persistence.prune(now)
        except Exception:
            _log.exception("pruning old messages failed")
            return
        _log.info("pruned %d old messages", pruned)


def _set_endpoints(instance: Extension[Any], endpoints: Mapping[EndpointRef, BaseModel]) -> None:
    provider: object = instance  # see Installation.provider
    if isinstance(provider, EndpointProvider):
        provider.set_endpoints(endpoints)


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"
