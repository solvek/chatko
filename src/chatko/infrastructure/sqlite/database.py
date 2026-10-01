"""The SQLite connection: one per hub, with its schema migrated and its transactions serialized."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Self

import aiosqlite

from chatko.infrastructure.sqlite.migrations import MIGRATIONS


class SchemaError(Exception):
    """The database was written by a newer chatko than this one."""


class Database:
    """One connection shared by the repositories. Every use is a transaction under a lock, so the
    awaits inside one never interleave with another coroutine's statements."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path | str) -> Self:
        """Open (and create) the database at `path`, or in memory for `":memory:"`, and bring its
        schema up to date."""
        connection = await aiosqlite.connect(path, isolation_level=None)
        try:
            await connection.execute("PRAGMA foreign_keys = ON")
            if str(path) != ":memory:":
                await connection.execute("PRAGMA journal_mode = WAL")
            database = cls(connection)
            await database._migrate()
        except BaseException:
            await connection.close()
            raise
        return database

    async def close(self) -> None:
        await self._connection.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """The connection inside a transaction: committed if the block ends, rolled back if it
        raises."""
        async with self._lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                await self._connection.execute("ROLLBACK")
                raise
            await self._connection.execute("COMMIT")

    async def version(self) -> int:
        async with self._connection.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        return 0 if row is None else int(row[0])

    async def _migrate(self) -> None:
        current = await self.version()
        if current > len(MIGRATIONS):
            raise SchemaError(
                f"the database has schema version {current}, this chatko knows {len(MIGRATIONS)}"
            )
        for number, script in enumerate(MIGRATIONS[current:], start=current + 1):
            # executescript runs the whole script, the version included, in one transaction.
            await self._connection.executescript(
                f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;"
            )
