"""The repository ports: the same tests run against the in-memory fakes and against SQLite."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from chatko.application.ports import (
    AccountRegistry,
    HistoryChanges,
    HistoryRepository,
    MessageRepository,
    OutboxRepository,
)
from chatko.application.testing import InMemoryAccounts, InMemoryHistoryStore, InMemoryStore
from chatko.domain import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Author,
    Delivery,
    EndpointRef,
    Message,
    MessageId,
    Person,
    Target,
    fingerprint,
)
from chatko.infrastructure.sqlite import Database, SqliteAccounts, SqliteHistory, SqliteStore

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TG = EndpointRef("tg", "family.tg")
RADIO = EndpointRef("mesh", "family.radio")
CHANNEL = EndpointRef("mesh", "family.channel")
ADA = Account(AccountKey("tg", "1"), "Ада", "A")
NODE = AccountKey("meshtastic", "!a1b2c3d4")
FP1 = fingerprint("Ada", "one")
FP2 = fingerprint("Ada", "two")


@dataclass
class Repos:
    messages: MessageRepository
    outbox: OutboxRepository
    history: HistoryRepository
    accounts: AccountRegistry


@pytest.fixture(params=["memory", "sqlite"])
async def repos(request: pytest.FixtureRequest) -> AsyncIterator[Repos]:
    if request.param == "memory":
        memory = InMemoryStore()
        yield Repos(memory, memory, InMemoryHistoryStore(), InMemoryAccounts())
        return
    database = await Database.open(":memory:")
    store = SqliteStore(database)
    yield Repos(store, store, SqliteHistory(database), SqliteAccounts(database))
    await database.close()


def message(number: int = 1, **fields: object) -> Message:
    values: dict[str, object] = {
        "id": MessageId(f"m{number}"),
        "endpoint": TG,
        "transport_id": f"t{number}",
        "author": Author(ADA),
        "text": "hello",
        "received_at": T0,
    }
    return Message(**(values | fields))  # type: ignore[arg-type]


def delivery(
    msg: Message, endpoint: EndpointRef = CHANNEL, recipient: str | None = None
) -> Delivery:
    return Delivery.of(msg, Target(endpoint), "Ada", T0, recipient)


class TestMessages:
    async def test_a_stored_message_is_found_by_its_transport_id(self, repos: Repos) -> None:
        msg = message()
        await repos.messages.add(msg, [])

        assert await repos.messages.contains(TG, "t1")
        assert not await repos.messages.contains(TG, "other")
        assert not await repos.messages.contains(CHANNEL, "t1")

    async def test_a_message_is_read_back_whole(self, repos: Repos) -> None:
        person = Person("Ada L", frozenset({ADA.key, AccountKey("meshtastic", "!a1")}))
        msg = message(
            author=Author(ADA, person, "relay"),
            text="Привіт 👋",
            received_at=T0.astimezone(timezone(timedelta(hours=3))),
            attachments=(Attachment(AttachmentKind.PHOTO), Attachment(AttachmentKind.VOICE)),
            from_recipient="!a1",
        )
        await repos.messages.add(msg, [])

        assert await repos.messages.get(msg.id) == msg

    async def test_an_account_without_names_is_read_back_as_it_was(self, repos: Repos) -> None:
        msg = message(author=Author(Account(AccountKey("meshtastic", "!a1"))))
        await repos.messages.add(msg, [])

        assert await repos.messages.get(msg.id) == msg

    async def test_a_message_that_is_not_stored_is_a_key_error(self, repos: Repos) -> None:
        with pytest.raises(KeyError):
            await repos.messages.get(MessageId("nope"))

    async def test_a_copy_stores_nothing(self, repos: Repos) -> None:
        first = message(1)
        copy = message(2, transport_id="t1")
        await repos.messages.add(first, [delivery(first)])

        assert not await repos.messages.add(copy, [delivery(copy)])

        assert [d.message_id for d in await repos.outbox.pending()] == [first.id]
        with pytest.raises(KeyError):
            await repos.messages.get(copy.id)

    async def test_the_same_transport_id_at_another_endpoint_is_not_a_copy(
        self, repos: Repos
    ) -> None:
        await repos.messages.add(message(1), [])

        assert await repos.messages.add(message(2, endpoint=CHANNEL, transport_id="t1"), [])

    async def test_a_delivery_of_another_message_is_refused_and_nothing_is_stored(
        self, repos: Repos
    ) -> None:
        msg, other = message(1), message(2)

        with pytest.raises(ValueError, match="not one of"):
            await repos.messages.add(msg, [delivery(other)])

        assert not await repos.messages.contains(TG, "t1")

    async def test_a_repeated_destination_is_refused_and_nothing_is_stored(
        self, repos: Repos
    ) -> None:
        msg = message()

        with pytest.raises(ValueError, match="repeat"):
            await repos.messages.add(msg, [delivery(msg), delivery(msg)])

        assert not await repos.messages.contains(TG, "t1")

    async def test_a_message_id_stored_twice_is_refused(self, repos: Repos) -> None:
        await repos.messages.add(message(1), [])

        with pytest.raises(ValueError, match="stored already"):
            await repos.messages.add(message(1, transport_id="other"), [])


class TestOutbox:
    async def test_pending_deliveries_come_in_the_order_they_were_stored(
        self, repos: Repos
    ) -> None:
        first, second = message(1), message(2)
        await repos.messages.add(
            first, [delivery(first, RADIO, "!b"), delivery(first, RADIO, "!a")]
        )
        await repos.messages.add(second, [delivery(second, CHANNEL)])

        assert [(d.message_id, d.endpoint, d.recipient) for d in await repos.outbox.pending()] == [
            (first.id, RADIO, "!b"),
            (first.id, RADIO, "!a"),
            (second.id, CHANNEL, None),
        ]

    async def test_a_saved_outcome_is_read_back(self, repos: Repos) -> None:
        msg = message()
        pending = delivery(msg, RADIO, "!a")
        await repos.messages.add(msg, [pending, delivery(msg, RADIO, "!b")])

        retry = pending.begin_attempt().retry(T0 + timedelta(seconds=30), "no ack")
        await repos.outbox.save(retry)

        stored = {d.recipient: d for d in await repos.outbox.pending()}
        assert stored["!a"] == retry
        assert stored["!a"].attempts == 1
        assert stored["!a"].last_error == "no ack"

    async def test_a_delivered_or_failed_delivery_is_no_longer_pending(self, repos: Repos) -> None:
        msg = message()
        sent, lost = delivery(msg, RADIO, "!a"), delivery(msg, RADIO, "!b")
        await repos.messages.add(msg, [sent, lost, delivery(msg, CHANNEL)])

        await repos.outbox.save(sent.begin_attempt().delivered(truncated=True))
        await repos.outbox.save(lost.begin_attempt().failed("gone"))

        assert [d.endpoint for d in await repos.outbox.pending()] == [CHANNEL]

    async def test_a_delivery_that_is_not_stored_cannot_be_saved(self, repos: Repos) -> None:
        msg = message()
        await repos.messages.add(msg, [delivery(msg)])

        with pytest.raises(KeyError):
            await repos.outbox.save(delivery(msg, RADIO))

    async def test_a_delivery_without_a_recipient_is_not_one_with_a_recipient(
        self, repos: Repos
    ) -> None:
        msg = message()
        await repos.messages.add(msg, [delivery(msg, RADIO, "!a")])

        with pytest.raises(KeyError):
            await repos.outbox.save(delivery(msg, RADIO))


class TestPruning:
    async def test_old_messages_without_pending_deliveries_are_forgotten(
        self, repos: Repos
    ) -> None:
        old, recent = message(1), message(2, received_at=T0 + timedelta(days=5))
        await repos.messages.add(old, [delivery(old).begin_attempt().delivered()])
        await repos.messages.add(recent, [])

        assert await repos.messages.prune(T0 + timedelta(days=1)) == 1

        assert not await repos.messages.contains(TG, "t1")
        with pytest.raises(KeyError):
            await repos.messages.get(old.id)
        assert await repos.messages.contains(TG, "t2")

    async def test_a_message_with_a_pending_delivery_stays(self, repos: Repos) -> None:
        old = message(1)
        await repos.messages.add(old, [delivery(old, RADIO, "!a"), delivery(old, RADIO, "!b")])
        await repos.outbox.save(delivery(old, RADIO, "!a").begin_attempt().delivered())

        assert await repos.messages.prune(T0 + timedelta(days=1)) == 0

        assert len(await repos.outbox.pending()) == 1
        assert await repos.messages.get(old.id) == old

    async def test_a_forgotten_message_leaves_no_deliveries_behind(self, repos: Repos) -> None:
        old = message(1)
        await repos.messages.add(old, [delivery(old).begin_attempt().failed("x")])
        await repos.messages.prune(T0 + timedelta(days=1))

        assert await repos.messages.add(message(2, transport_id="t1"), [delivery(message(2))])
        assert len(await repos.outbox.pending()) == 1


class TestHistory:
    async def test_it_starts_empty(self, repos: Repos) -> None:
        loaded = await repos.history.load(T0)

        assert not loaded

    async def test_what_was_recorded_is_loaded(self, repos: Repos) -> None:
        await repos.history.record(
            HistoryChanges(
                {(NODE, None): T0, (NODE, CHANNEL): T0 - timedelta(minutes=1)},
                [(T0, FP1), (T0 + timedelta(seconds=1), FP2)],
            )
        )

        loaded = await repos.history.load(T0 - timedelta(days=1))

        assert loaded.heard == {(NODE, None): T0, (NODE, CHANNEL): T0 - timedelta(minutes=1)}
        assert list(loaded.arrivals) == [(T0, FP1), (T0 + timedelta(seconds=1), FP2)]

    async def test_a_last_heard_time_never_moves_back(self, repos: Repos) -> None:
        await repos.history.record(HistoryChanges({(NODE, None): T0}))
        await repos.history.record(HistoryChanges({(NODE, None): T0 - timedelta(hours=1)}))
        await repos.history.record(HistoryChanges({(NODE, None): T0 + timedelta(hours=1)}))

        assert (await repos.history.load(T0)).heard == {(NODE, None): T0 + timedelta(hours=1)}

    async def test_only_arrivals_since_the_given_time_are_loaded(self, repos: Repos) -> None:
        await repos.history.record(
            HistoryChanges(arrivals=[(T0, FP1), (T0 + timedelta(hours=2), FP2)])
        )

        loaded = await repos.history.load(T0 + timedelta(hours=1))

        assert list(loaded.arrivals) == [(T0 + timedelta(hours=2), FP2)]

    async def test_pruning_forgets_old_arrivals_but_not_last_heard(self, repos: Repos) -> None:
        await repos.history.record(
            HistoryChanges({(NODE, None): T0}, [(T0, FP1), (T0 + timedelta(hours=2), FP2)])
        )

        await repos.history.prune(T0 + timedelta(hours=1))

        loaded = await repos.history.load(T0 - timedelta(days=1))
        assert list(loaded.arrivals) == [(T0 + timedelta(hours=2), FP2)]
        assert loaded.heard == {(NODE, None): T0}


class TestAccounts:
    async def test_the_first_time_an_account_is_noted_it_is_new(self, repos: Repos) -> None:
        assert await repos.accounts.note(ADA, T0)
        assert not await repos.accounts.note(ADA, T0 + timedelta(minutes=1))

    async def test_another_account_is_new(self, repos: Repos) -> None:
        await repos.accounts.note(ADA, T0)

        assert await repos.accounts.note(Account(AccountKey("tg", "2")), T0)

    async def test_an_account_noted_with_new_names_is_not_new_again(self, repos: Repos) -> None:
        await repos.accounts.note(ADA, T0)

        assert not await repos.accounts.note(Account(ADA.key, "Ada Lovelace"), T0)
