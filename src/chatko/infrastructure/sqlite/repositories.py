"""The repository ports over SQLite."""

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from chatko.application.ports import HistoryChanges
from chatko.domain import (
    Account,
    AccountKey,
    Delivery,
    DeliveryState,
    EndpointRef,
    Fingerprint,
    Message,
    MessageId,
)
from chatko.infrastructure.sqlite.codec import (
    decode_attachments,
    decode_author,
    decode_time,
    encode_attachments,
    encode_author,
    encode_time,
    endpoint_columns,
)
from chatko.infrastructure.sqlite.database import Database

_DELIVERY_COLUMNS = (
    "message_id, instance, endpoint, recipient, author_label, text, due_at, state, attempts, "
    "truncated, last_error"
)


class SqliteStore:
    """Implements `MessageRepository` and `OutboxRepository`."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def contains(self, endpoint: EndpointRef, transport_id: str) -> bool:
        async with self._database.transaction() as db:
            cursor = await db.execute(
                "SELECT 1 FROM messages WHERE instance = ? AND endpoint = ? AND transport_id = ?",
                (endpoint.instance, endpoint.name, transport_id),
            )
            return await cursor.fetchone() is not None

    async def add(self, message: Message, deliveries: Sequence[Delivery]) -> bool:
        keys = [_delivery_key(delivery) for delivery in deliveries]
        if len(set(keys)) != len(keys):
            raise ValueError(f"the deliveries of {message.id} repeat a destination")
        if any(delivery.message_id != message.id for delivery in deliveries):
            raise ValueError(f"a delivery is not one of {message.id}")
        async with self._database.transaction() as db:
            cursor = await db.execute(
                "SELECT 1 FROM messages WHERE instance = ? AND endpoint = ? AND transport_id = ?",
                (message.endpoint.instance, message.endpoint.name, message.transport_id),
            )
            if await cursor.fetchone() is not None:
                return False
            try:
                await db.execute(
                    "INSERT INTO messages (id, instance, endpoint, transport_id, received_at, "
                    "author, text, attachments, from_recipient) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message.id,
                        message.endpoint.instance,
                        message.endpoint.name,
                        message.transport_id,
                        encode_time(message.received_at),
                        encode_author(message.author),
                        message.text,
                        encode_attachments(message.attachments),
                        message.from_recipient,
                    ),
                )
                await db.executemany(
                    f"INSERT INTO deliveries ({_DELIVERY_COLUMNS}) "  # noqa: S608
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [_delivery_row(delivery) for delivery in deliveries],
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"message {message.id} is stored already: {error}") from error
            return True

    async def get(self, message_id: MessageId) -> Message:
        async with self._database.transaction() as db:
            cursor = await db.execute(
                "SELECT id, instance, endpoint, transport_id, received_at, author, text, "
                "attachments, from_recipient FROM messages WHERE id = ?",
                (message_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"no message {message_id}")
        return Message(
            id=MessageId(row[0]),
            endpoint=EndpointRef(row[1], row[2]),
            transport_id=row[3],
            author=decode_author(row[5]),
            text=row[6],
            received_at=decode_time(row[4]),
            attachments=decode_attachments(row[7]),
            from_recipient=row[8],
        )

    async def prune(self, before: datetime) -> int:
        async with self._database.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM messages WHERE received_at < ? AND NOT EXISTS "
                "(SELECT 1 FROM deliveries WHERE message_id = messages.id AND state = ?)",
                (encode_time(before), DeliveryState.PENDING.value),
            )
            return cursor.rowcount

    async def pending(self) -> list[Delivery]:
        async with self._database.transaction() as db:
            cursor = await db.execute(
                f"SELECT {_DELIVERY_COLUMNS} FROM deliveries "  # noqa: S608
                "WHERE state = ? ORDER BY seq",
                (DeliveryState.PENDING.value,),
            )
            rows = await cursor.fetchall()
        return [_delivery(row) for row in rows]

    async def save(self, delivery: Delivery) -> None:
        async with self._database.transaction() as db:
            cursor = await db.execute(
                "UPDATE deliveries SET author_label = ?, text = ?, due_at = ?, state = ?, "
                "attempts = ?, truncated = ?, last_error = ? "
                "WHERE message_id = ? AND instance = ? AND endpoint = ? AND recipient = ?",
                (*_delivery_row(delivery)[4:], *_delivery_row(delivery)[:4]),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no delivery of {delivery.message_id} to {delivery.destination}")


class SqliteHistory:
    """Implements `HistoryRepository`."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def load(self, since: datetime) -> HistoryChanges:
        async with self._database.transaction() as db:
            heard_rows = await (
                await db.execute("SELECT kind, external_id, instance, endpoint, at FROM heard")
            ).fetchall()
            arrival_rows = await (
                await db.execute(
                    "SELECT at, fingerprint FROM arrivals WHERE at >= ? ORDER BY at, seq",
                    (encode_time(since),),
                )
            ).fetchall()
        heard = {
            (AccountKey(kind, external_id), EndpointRef(instance, name) if instance else None): (
                decode_time(at)
            )
            for kind, external_id, instance, name, at in heard_rows
        }
        return HistoryChanges(
            heard, [(decode_time(at), Fingerprint(value)) for at, value in arrival_rows]
        )

    async def record(self, changes: HistoryChanges) -> None:
        async with self._database.transaction() as db:
            await db.executemany(
                "INSERT INTO heard (kind, external_id, instance, endpoint, at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (kind, external_id, instance, endpoint) "
                "DO UPDATE SET at = excluded.at WHERE excluded.at > heard.at",
                [
                    (
                        account.kind,
                        account.external_id,
                        *endpoint_columns(endpoint),
                        encode_time(at),
                    )
                    for (account, endpoint), at in changes.heard.items()
                ],
            )
            await db.executemany(
                "INSERT INTO arrivals (fingerprint, at) VALUES (?, ?)",
                [(fingerprint.value, encode_time(at)) for at, fingerprint in changes.arrivals],
            )

    async def prune(self, before: datetime) -> None:
        async with self._database.transaction() as db:
            await db.execute("DELETE FROM arrivals WHERE at < ?", (encode_time(before),))


class SqliteAccounts:
    """Implements `AccountRegistry`."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def note(self, account: Account, at: datetime, *, at_site: bool = False) -> bool:
        async with self._database.transaction() as db:
            cursor = await db.execute(
                "SELECT last_seen, first_at_site FROM accounts WHERE kind = ? AND external_id = ?",
                (account.key.kind, account.key.external_id),
            )
            row = await cursor.fetchone()
            when = encode_time(at)
            if row is None:
                await db.execute(
                    "INSERT INTO accounts (kind, external_id, display_name, short_name, "
                    "first_seen, last_seen, first_at_site) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        account.key.kind,
                        account.key.external_id,
                        account.display_name,
                        account.short_name,
                        when,
                        when,
                        when if at_site else None,
                    ),
                )
                return at_site
            new = at_site and row[1] is None
            await db.execute(
                "UPDATE accounts SET display_name = ?, short_name = ?, last_seen = ?, "
                "first_at_site = ? WHERE kind = ? AND external_id = ?",
                (
                    account.display_name,
                    account.short_name,
                    max(when, row[0]),
                    when if new else row[1],
                    account.key.kind,
                    account.key.external_id,
                ),
            )
            return new


def _delivery_key(delivery: Delivery) -> tuple[str, str, str, str]:
    return (
        delivery.message_id,
        delivery.endpoint.instance,
        delivery.endpoint.name,
        delivery.recipient or "",
    )


def _delivery_row(delivery: Delivery) -> tuple[object, ...]:
    return (
        *_delivery_key(delivery),
        delivery.author_label,
        delivery.text,
        encode_time(delivery.due_at),
        delivery.state.value,
        delivery.attempts,
        int(delivery.truncated),
        delivery.last_error,
    )


def _delivery(row: Sequence[object]) -> Delivery:
    return Delivery(
        message_id=MessageId(str(row[0])),
        endpoint=EndpointRef(str(row[1]), str(row[2])),
        recipient=str(row[3]) or None,
        author_label=str(row[4]),
        text=str(row[5]),
        due_at=decode_time(str(row[6])),
        state=DeliveryState(str(row[7])),
        attempts=int(str(row[8])),
        truncated=bool(row[9]),
        last_error=None if row[10] is None else str(row[10]),
    )
