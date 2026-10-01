"""A routing history that a test fills in."""

from datetime import datetime

from chatko.domain import AccountKey, EndpointRef, Fingerprint


class FakeHistory:
    """Implements `RoutingHistory` from what a test tells it with `hear` and `see`."""

    def __init__(self) -> None:
        self._heard: dict[tuple[AccountKey, EndpointRef | None], datetime] = {}
        self._seen: dict[Fingerprint, list[datetime]] = {}

    def hear(self, account: AccountKey, at: datetime, endpoint: EndpointRef | None = None) -> None:
        """The hub heard `account` at `at`, at `endpoint` if given."""
        self._heard[account, endpoint] = at

    def see(self, fingerprint: Fingerprint, at: datetime) -> None:
        """A message with this fingerprint arrived at `at`."""
        self._seen.setdefault(fingerprint, []).append(at)

    def last_heard(self, account: AccountKey, endpoint: EndpointRef | None) -> datetime | None:
        times = [
            at
            for (heard, where), at in self._heard.items()
            if heard == account and (endpoint is None or where == endpoint)
        ]
        return max(times, default=None)

    def seen_since(self, fingerprint: Fingerprint, since: datetime) -> bool:
        return any(at >= since for at in self._seen.get(fingerprint, ()))
