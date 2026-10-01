"""The hub's in-memory routing history: last heard and recent fingerprints."""

from datetime import UTC, datetime, timedelta

import pytest

from chatko.application.history import HistoryPersistence, HubHistory
from chatko.application.ports import HistoryChanges
from chatko.application.testing import InMemoryHistoryStore
from chatko.domain import AccountKey, EndpointRef, fingerprint

NODE = AccountKey("meshtastic", "!a1b2c3d4")
CHANNEL = EndpointRef("mesh", "family.channel")
RADIO = EndpointRef("mesh", "family.radio")
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FP = fingerprint("NatAda", "Привіт")


def test_an_account_never_heard_has_no_time() -> None:
    assert HubHistory().last_heard(NODE, None) is None


def test_the_latest_time_is_kept_per_endpoint_and_anywhere() -> None:
    history = HubHistory()
    history.hear(NODE, T0 + timedelta(minutes=5), CHANNEL)
    history.hear(NODE, T0, CHANNEL)  # told late: an older time does not win
    history.hear(NODE, T0 + timedelta(minutes=1), RADIO)

    assert history.last_heard(NODE, CHANNEL) == T0 + timedelta(minutes=5)
    assert history.last_heard(NODE, RADIO) == T0 + timedelta(minutes=1)
    assert history.last_heard(NODE, None) == T0 + timedelta(minutes=5)


def test_heard_anywhere_is_not_heard_at_an_endpoint() -> None:
    history = HubHistory()
    history.hear(NODE, T0)

    assert history.last_heard(NODE, None) == T0
    assert history.last_heard(NODE, CHANNEL) is None


def test_seen_since_compares_with_the_latest_arrival() -> None:
    history = HubHistory()
    history.see(FP, T0)
    history.see(FP, T0 + timedelta(minutes=10))

    assert history.seen_since(FP, T0 + timedelta(minutes=10))
    assert not history.seen_since(FP, T0 + timedelta(minutes=11))
    assert not history.seen_since(fingerprint("NatAda", "інше"), T0)


def test_fingerprints_older_than_the_retention_are_forgotten() -> None:
    history = HubHistory(retention=timedelta(hours=1))
    history.see(FP, T0)
    other = fingerprint("AdaLov", "hi")

    history.see(other, T0 + timedelta(hours=1, seconds=1))

    assert not history.seen_since(FP, T0 - timedelta(days=1))
    assert history.seen_since(other, T0)


def test_a_fingerprint_seen_again_is_kept_by_its_latest_arrival() -> None:
    history = HubHistory(retention=timedelta(hours=1))
    history.see(FP, T0)
    history.see(FP, T0 + timedelta(minutes=50))

    history.see(fingerprint("AdaLov", "hi"), T0 + timedelta(minutes=70))

    assert history.seen_since(FP, T0 + timedelta(minutes=50))


def test_the_retention_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        HubHistory(retention=timedelta(0))


class FlakyRepository:
    """A `HistoryRepository` that keeps what it is given and can be made to fail."""

    def __init__(self) -> None:
        self.saved = InMemoryHistoryStore()
        self.failing = False

    async def load(self, since: datetime) -> HistoryChanges:
        return await self.saved.load(since)

    async def record(self, changes: HistoryChanges) -> None:
        if self.failing:
            raise OSError("disk full")
        await self.saved.record(changes)

    async def prune(self, before: datetime) -> None:
        await self.saved.prune(before)


class TestPersistence:
    async def test_only_what_is_new_is_saved(self) -> None:
        history, repository = HubHistory(), FlakyRepository()
        persistence = HistoryPersistence(history, repository)
        history.hear(NODE, T0, CHANNEL)
        history.see(FP, T0)
        await persistence.flush()
        history.hear(NODE, T0 + timedelta(minutes=1), CHANNEL)
        await persistence.flush()

        assert repository.saved.heard[(NODE, CHANNEL)] == T0 + timedelta(minutes=1)
        assert repository.saved.arrivals == [(T0, FP)]

    async def test_a_restart_restores_what_was_saved_and_saves_none_of_it_again(self) -> None:
        repository = FlakyRepository()
        before = HubHistory()
        saved = HistoryPersistence(before, repository)
        before.hear(NODE, T0, CHANNEL)
        before.see(FP, T0)
        await saved.flush()

        after = HubHistory()
        restarted = HistoryPersistence(after, repository)
        await restarted.load(T0 + timedelta(days=1))
        await restarted.flush()

        assert after.last_heard(NODE, CHANNEL) == T0
        assert after.seen_since(FP, T0)
        assert repository.saved.arrivals == [(T0, FP)]

    async def test_arrivals_older_than_the_retention_are_not_restored(self) -> None:
        repository = FlakyRepository()
        await repository.record(HistoryChanges(arrivals=[(T0, FP)]))
        history = HubHistory(timedelta(days=1))

        await HistoryPersistence(history, repository).load(T0 + timedelta(days=2))

        assert not history.seen_since(FP, T0 - timedelta(days=1))

    async def test_a_failed_save_is_tried_again_with_the_next_changes(self) -> None:
        history, repository = HubHistory(), FlakyRepository()
        persistence = HistoryPersistence(history, repository)
        history.see(FP, T0)
        repository.failing = True
        with pytest.raises(OSError, match="disk full"):
            await persistence.flush()
        repository.failing = False
        history.hear(NODE, T0, None)

        await persistence.flush()

        assert repository.saved.arrivals == [(T0, FP)]
        assert repository.saved.heard == {(NODE, None): T0}

    async def test_pruning_drops_what_the_retention_has_passed(self) -> None:
        history, repository = HubHistory(timedelta(days=1)), FlakyRepository()
        persistence = HistoryPersistence(history, repository)
        history.see(FP, T0)
        await persistence.flush()

        await persistence.prune(T0 + timedelta(days=2))

        assert repository.saved.arrivals == []
