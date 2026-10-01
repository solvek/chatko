"""The routing script's code from a file: `config/routing.py` (docs/design.md §9)."""

import asyncio
from collections.abc import Callable
from pathlib import Path


class FileScriptSource:
    """Implements `RoutingScriptSource` over a file. A missing file is no script."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def origin(self) -> str:
        return str(self.path)

    async def read(self) -> str | None:
        return await asyncio.to_thread(self._read)

    def _read(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None


class ConfiguredScriptSource:
    """Implements `RoutingScriptSource` for the script the current config names (`routing:`),
    relative to the config's directory. Without a name there is no script, and when the name
    changes the next read follows it."""

    def __init__(self, directory: Path, name: Callable[[], str | None]) -> None:
        self.directory = directory
        self._name = name

    @property
    def path(self) -> Path | None:
        name = self._name()
        return None if name is None else self.directory / name

    @property
    def origin(self) -> str:
        path = self.path
        return "<no routing script>" if path is None else str(path)

    async def read(self) -> str | None:
        path = self.path
        return None if path is None else await FileScriptSource(path).read()
