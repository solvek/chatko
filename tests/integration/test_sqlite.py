"""What is particular to SQLite: the file, the migrations, the transactions."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from chatko.application.history import HistoryPersistence, HubHistory
from chatko.domain import (
    Account,
    AccountKey,
    Author,
    Delivery,
    EndpointRef,
    Message,
    MessageId,
    Target,
    fingerprint,
)
from chatko.infrastructure.sqlite import (
    Database,
    SchemaError,
    SqliteAccounts,
    SqliteHistory,
    SqliteStore,
)
from chatko.infrastructure.sqlite.migrations import MIGRATIONS

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TG = EndpointRef("telegram", "family.telegram")
CHANNEL = EndpointRef("mesh", "family.channel")
ADA = Account(AccountKey("telegram", "1"), "Ада")
NODE = AccountKey("meshtastic", "!a1b2c3d4")


def message(number: int) -> Message:
    return Message(MessageId(f"m{number}"), TG, f"t{number}", Author(ADA), "hi", T0)


async def test_a_new_database_is_at_the_latest_schema(tmp_path: Path) -> None:
    database = await Database.open(tmp_path / "chatko.db")

    assert await database.version() == len(MIGRATIONS)
    await database.close()


async def test_reopening_keeps_the_data_and_applies_no_migration_twice(tmp_path: Path) -> None:
    path = tmp_path / "chatko.db"
    database = await Database.open(path)
    msg = message(1)
    await SqliteStore(database).add(msg, [Delivery.of(msg, Target(CHANNEL), "Ada", T0)])
    await database.close()

    reopened = await Database.open(path)

    store = SqliteStore(reopened)
    assert await store.get(msg.id) == msg
    assert len(await store.pending()) == 1
    await reopened.close()


async def test_accounts_known_before_migration_2_are_not_new_at_a_site(tmp_path: Path) -> None:
    # They were told about under the old rule, or the admin did not ask (D66).
    path = tmp_path / "chatko.db"
    async with aiosqlite.connect(path) as connection:
        await connection.executescript(MIGRATIONS[0] + "PRAGMA user_version = 1;")
        await connection.execute(
            "INSERT INTO accounts VALUES ('telegram', '1', 'Ада', NULL, ?, ?)",
            ("2026-10-01T12:00:00.000000Z", "2026-10-01T12:00:00.000000Z"),
        )
        await connection.commit()

    database = await Database.open(path)
    accounts = SqliteAccounts(database)

    assert await database.version() == len(MIGRATIONS)
    assert not await accounts.note(ADA, T0, at_site=True)
    assert await accounts.note(Account(AccountKey("telegram", "2")), T0, at_site=True)
    await database.close()


async def test_a_database_of_a_newer_chatko_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "chatko.db"
    async with aiosqlite.connect(path) as connection:
        await connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")

    with pytest.raises(SchemaError, match="schema version"):
        await Database.open(path)


async def test_a_failed_transaction_is_rolled_back() -> None:
    database = await Database.open(":memory:")
    store = SqliteStore(database)
    msg = message(1)

    async def fail_after_writing() -> None:
        async with database.transaction() as db:
            await db.execute(
                "INSERT INTO accounts VALUES ('telegram', '9', 'x', NULL, 'a', 'a', NULL)"
            )
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await fail_after_writing()

    assert await store.add(msg, [])
    async with database.transaction() as db:
        row = await (await db.execute("SELECT COUNT(*) FROM accounts")).fetchone()
    assert row is not None
    assert row[0] == 0
    await database.close()


@pytest.mark.parametrize("steps", range(6))
async def test_a_transaction_cancelled_at_any_step_leaves_the_database_usable(steps: int) -> None:
    database = await Database.open(":memory:")
    store = SqliteStore(database)
    try:
        adding = asyncio.create_task(store.add(message(1), []))
        for _ in range(steps):  # wherever it waits: BEGIN, a statement, COMMIT
            await asyncio.sleep(0)
        adding.cancel()
        await asyncio.wait({adding})

        assert await store.add(message(2), [])
    finally:
        await database.close()


async def test_concurrent_writers_do_not_interleave() -> None:
    database = await Database.open(":memory:")
    store = SqliteStore(database)

    results = await asyncio.gather(*(store.add(message(n), []) for n in range(1, 21)))

    assert all(results)
    assert not await store.add(message(3), [])
    await database.close()


async def test_a_history_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "chatko.db"
    fp = fingerprint("Ada", "hi")
    database = await Database.open(path)
    history = HubHistory()
    persistence = HistoryPersistence(history, SqliteHistory(database))
    history.hear(NODE, T0, CHANNEL)
    history.see(fp, T0)
    await persistence.flush()
    await database.close()

    database = await Database.open(path)
    restored = HubHistory()
    await HistoryPersistence(restored, SqliteHistory(database)).load(T0 + timedelta(hours=1))

    assert restored.last_heard(NODE, CHANNEL) == T0
    assert restored.last_heard(NODE, None) == T0
    assert restored.seen_since(fp, T0 - timedelta(minutes=1))
    await database.close()
