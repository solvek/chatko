"""`chatko run`: the composition root. It puts the infrastructure under the `HubRuntime`, watches
the config files and runs until it is told to stop (docs/architecture.md §5.5)."""

import asyncio
import logging
import signal
from collections.abc import Mapping
from pathlib import Path

from chatko.application.config import ExtensionTypes
from chatko.application.runtime import HubPorts, HubRuntime, HubSettings, StartError
from chatko.infrastructure.clock import RandomIds, SystemClock
from chatko.infrastructure.config import FileConfigLoader
from chatko.infrastructure.routing_script import ConfiguredScriptSource
from chatko.infrastructure.sqlite import Database, SqliteAccounts, SqliteHistory, SqliteStore
from chatko.infrastructure.watcher import watch_files

_log = logging.getLogger("chatko.run")

DATABASE_FILE = "chatko.sqlite3"
"""The hub's state in the data directory."""


async def serve(config: Path, env: Mapping[str, str], types: ExtensionTypes, *, data: Path) -> int:
    """Run the hub until SIGINT or SIGTERM; returns the exit code (see `run_hub`).

    Where the event loop cannot handle signals (Windows), Ctrl+C interrupts `asyncio.run`, which
    cancels the hub; it still stops in order.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            continue
        handled.append(signum)
    try:
        return await run_hub(config, env, types, data=data, stop=stop)
    finally:
        for signum in handled:
            loop.remove_signal_handler(signum)


async def run_hub(
    config: Path,
    env: Mapping[str, str],
    types: ExtensionTypes,
    *,
    data: Path,
    stop: asyncio.Event,
    settings: HubSettings | None = None,
) -> int:
    """Run the hub with the config file `config` and its state in the directory `data`, until
    `stop` is set. Returns 0 after a stop, and 1 if the hub cannot start (no valid config; the
    log says why)."""
    await asyncio.to_thread(data.mkdir, parents=True, exist_ok=True)
    database = await Database.open(data / DATABASE_FILE)
    try:
        store = SqliteStore(database)

        def routing() -> str | None:
            applied = runtime.config
            return None if applied is None else applied.routing

        script = ConfiguredScriptSource(config.parent, routing)
        runtime = HubRuntime(
            ports=HubPorts(
                clock=SystemClock(),
                ids=RandomIds(),
                messages=store,
                outbox=store,
                history=SqliteHistory(database),
                accounts=SqliteAccounts(database),
                config=FileConfigLoader(config, env),
                script=script,
            ),
            types=types,
            settings=settings,
        )
        try:
            await runtime.start()
        except StartError as error:
            _log.error("the hub did not start: %s", error)
            return 1
        try:
            await _watch(runtime, config, script, stop)
        finally:
            await runtime.stop()
        return 0
    finally:
        await database.close()


async def _watch(
    runtime: HubRuntime, config: Path, script: ConfiguredScriptSource, stop: asyncio.Event
) -> None:
    """Refresh the hub whenever the config or the routing script changes, until `stop` is set.
    When the config names another routing script, the watching moves to it."""
    while not stop.is_set():
        paths = _watched(config, script)
        moved = asyncio.Event()

        async def on_change(paths: list[Path] = paths, moved: asyncio.Event = moved) -> None:
            await runtime.refresh()
            if _watched(config, script) != paths:
                moved.set()

        relay = asyncio.create_task(_relay(stop, moved))
        try:
            await watch_files(paths, on_change, moved)
        finally:
            relay.cancel()


def _watched(config: Path, script: ConfiguredScriptSource) -> list[Path]:
    """The config file, and the routing script if it is named and its directory exists."""
    path = script.path
    return [config] if path is None or not path.parent.is_dir() else [config, path]


async def _relay(stop: asyncio.Event, moved: asyncio.Event) -> None:
    await stop.wait()
    moved.set()
