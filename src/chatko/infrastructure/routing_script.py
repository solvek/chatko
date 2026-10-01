"""The routing script's code from a file: `config/routing.py` (docs/design.md §9)."""

import asyncio
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
