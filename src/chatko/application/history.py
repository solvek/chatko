"""What the hub remembers in memory for the routing script: last heard, recent fingerprints."""

from collections import deque
from datetime import datetime, timedelta

from chatko.application.ports import HistoryChanges, HistoryRepository
from chatko.domain import AccountKey, EndpointRef, Fingerprint

DEFAULT_RETENTION = timedelta(days=7)


class HubHistory:
    """Implements `chatko.routing_api.RoutingHistory` from what the hub has heard and received.

    It keeps, per account, the last time it was heard anywhere and at each endpoint, and the
    fingerprints of the messages received within `retention`: `seen` cannot look further back.
    """

    def __init__(self, retention: timedelta = DEFAULT_RETENTION) -> None:
        if retention <= timedelta(0):
            raise ValueError("the retention must be positive")
        self.retention = retention
        self._heard: dict[tuple[AccountKey, EndpointRef | None], datetime] = {}
        self._fingerprints: dict[Fingerprint, deque[datetime]] = {}
        self._arrivals: deque[tuple[datetime, Fingerprint]] = deque()
        self._unsaved_heard: dict[tuple[AccountKey, EndpointRef | None], datetime] = {}
        self._unsaved_arrivals: list[tuple[datetime, Fingerprint]] = []

    def hear(self, account: AccountKey, at: datetime, endpoint: EndpointRef | None = None) -> None:
        """The hub heard `account` at time `at`, at `endpoint` if it was heard at one."""
        keys = [(account, None)] if endpoint is None else [(account, None), (account, endpoint)]
        for key in keys:
            known = self._heard.get(key)
            if known is None or known < at:
                self._heard[key] = at
                self._unsaved_heard[key] = at

    def see(self, fingerprint: Fingerprint, at: datetime) -> None:
        """A message with this fingerprint arrived at `at`; arrivals are told in order of time."""
        self._forget_before(at - self.retention)
        self._fingerprints.setdefault(fingerprint, deque()).append(at)
        self._arrivals.append((at, fingerprint))
        self._unsaved_arrivals.append((at, fingerprint))

    def restore(self, saved: HistoryChanges) -> None:
        """Take what a repository kept, before anything is heard. Nothing restored is unsaved."""
        for (account, endpoint), at in saved.heard.items():
            self._heard[(account, endpoint)] = at
        for at, fingerprint in sorted(saved.arrivals, key=lambda arrival: arrival[0]):
            self._fingerprints.setdefault(fingerprint, deque()).append(at)
            self._arrivals.append((at, fingerprint))

    def take_unsaved(self) -> HistoryChanges:
        """What was heard or seen since the last call, for a repository; it is then saved."""
        changes = HistoryChanges(dict(self._unsaved_heard), tuple(self._unsaved_arrivals))
        self._unsaved_heard.clear()
        self._unsaved_arrivals.clear()
        return changes

    def last_heard(self, account: AccountKey, endpoint: EndpointRef | None) -> datetime | None:
        return self._heard.get((account, endpoint))

    def seen_since(self, fingerprint: Fingerprint, since: datetime) -> bool:
        times = self._fingerprints.get(fingerprint)
        return times is not None and times[-1] >= since

    def _forget_before(self, limit: datetime) -> None:
        while self._arrivals and self._arrivals[0][0] < limit:
            _, fingerprint = self._arrivals.popleft()
            times = self._fingerprints[fingerprint]
            times.popleft()
            if not times:
                del self._fingerprints[fingerprint]


class HistoryPersistence:
    """Keeps a `HubHistory` in a repository: loads it at start, saves what is new, prunes the old.

    The `HubRuntime` calls `flush` every few seconds and at stop; if the repository fails the
    changes are kept and the next flush saves them with the new ones.
    """

    def __init__(self, history: HubHistory, repository: HistoryRepository) -> None:
        self._history = history
        self._repository = repository
        self._unsaved = HistoryChanges()

    async def load(self, now: datetime) -> None:
        self._history.restore(await self._repository.load(now - self._history.retention))

    async def flush(self) -> None:
        taken = self._history.take_unsaved()
        pending = HistoryChanges(
            {**self._unsaved.heard, **taken.heard},
            (*self._unsaved.arrivals, *taken.arrivals),
        )
        if not pending:
            return
        self._unsaved = pending
        await self._repository.record(pending)
        self._unsaved = HistoryChanges()

    async def prune(self, now: datetime) -> None:
        await self._repository.prune(now - self._history.retention)
