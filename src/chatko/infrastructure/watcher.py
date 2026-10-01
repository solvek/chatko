"""Watching the config files for changes (docs/design.md §10)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import watchfiles

_log = logging.getLogger("chatko.watcher")


async def watch_files(
    paths: Sequence[Path],
    on_change: Callable[[], Awaitable[None]],
    stop: asyncio.Event,
    *,
    debounce_ms: int = 400,
) -> None:
    """Call `on_change` whenever one of the files is created, changed or removed, until `stop` is
    set. A burst of events (an editor saving through a temporary file) makes one call.

    The directories are watched rather than the files, so that a file replaced by a rename or
    created later is noticed. An error in `on_change` is logged and the watching goes on.
    """
    wanted = {path.resolve() for path in paths}
    directories = {path.parent for path in wanted}
    async for changes in watchfiles.awatch(*directories, stop_event=stop, debounce=debounce_ms):
        if not any(Path(changed) in wanted for _, changed in changes):
            continue
        try:
            await on_change()
        except Exception:
            _log.exception("handling a change of the config files failed")
