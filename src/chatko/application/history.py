"""What the hub remembers in memory for the routing script: last heard, recent fingerprints."""

from collections import deque
from datetime import datetime, timedelta

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

    def hear(self, account: AccountKey, at: datetime, endpoint: EndpointRef | None = None) -> None:
        """The hub heard `account` at time `at`, at `endpoint` if it was heard at one."""
        keys = [(account, None)] if endpoint is None else [(account, None), (account, endpoint)]
        for key in keys:
            known = self._heard.get(key)
            if known is None or known < at:
                self._heard[key] = at

    def see(self, fingerprint: Fingerprint, at: datetime) -> None:
        """A message with this fingerprint arrived at `at`; arrivals are told in order of time."""
        self._forget_before(at - self.retention)
        self._fingerprints.setdefault(fingerprint, deque()).append(at)
        self._arrivals.append((at, fingerprint))

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
