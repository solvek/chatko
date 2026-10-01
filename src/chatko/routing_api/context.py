"""The installation as the routing script sees it (docs/architecture.md §4)."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Protocol

from chatko.domain import AccountKey, EndpointRef, Fingerprint, Group, Person, Topology


class RoutingHistory(Protocol):
    """What the hub remembers about earlier messages and accounts, for `RoutingContext`.

    The core serves it from memory, because a routing script may not wait for I/O, and the test
    kit fakes it. Scripts use the methods of `RoutingContext` instead.
    """

    def last_heard(self, account: AccountKey, endpoint: EndpointRef | None) -> datetime | None:
        """When the hub last heard `account` at `endpoint`, or anywhere if it is `None`."""
        ...

    def seen_since(self, fingerprint: Fingerprint, since: datetime) -> bool:
        """Whether a message with this fingerprint, other than the one being routed, arrived
        at or after `since`."""
        ...


class _NoHistory:
    def last_heard(self, account: AccountKey, endpoint: EndpointRef | None) -> datetime | None:
        del account, endpoint
        return None

    def seen_since(self, fingerprint: Fingerprint, since: datetime) -> bool:
        del fingerprint, since
        return False


class RoutingContext:
    """The installation as the routing script sees it, read-only. The core makes one for each
    message it routes.

    `extension_types` maps each extension instance to its type (`tg` → `telegram`), and
    `recipients` each endpoint that has recipients to them (`EndpointProvider.recipients`).
    """

    __slots__ = ("_extension_types", "_history", "_now", "_recipients", "_topology")

    def __init__(
        self,
        topology: Topology,
        *,
        now: datetime,
        extension_types: Mapping[str, str] | None = None,
        recipients: Mapping[EndpointRef, tuple[str, ...]] | None = None,
        history: RoutingHistory | None = None,
    ) -> None:
        if now.utcoffset() is None:
            raise ValueError("the routing clock must be timezone-aware")
        self._topology = topology
        self._now = now
        self._extension_types = MappingProxyType(dict(extension_types or {}))
        self._recipients = MappingProxyType(dict(recipients or {}))
        self._history: RoutingHistory = _NoHistory() if history is None else history

    @property
    def now(self) -> datetime:
        """The time the routing of this message started."""
        return self._now

    @property
    def groups(self) -> tuple[Group, ...]:
        return self._topology.groups

    @property
    def sources(self) -> Mapping[str, EndpointRef]:
        return self._topology.sources

    @property
    def people(self) -> tuple[Person, ...]:
        return self._topology.people

    def group(self, name: str) -> Group:
        """The group with this name; `KeyError` if there is none."""
        return self._topology.group(name)

    def group_of(self, endpoint: EndpointRef) -> Group | None:
        """The group the endpoint is a site of; `None` for a source."""
        return self._topology.group_of(endpoint)

    def source(self, name: str) -> EndpointRef:
        """The source with this name (`longfast`); `KeyError` if there is none."""
        return self._topology.source(name)

    def endpoint(self, name: str) -> EndpointRef:
        """The site or source with this name (`family.tg`, `longfast`); `KeyError` if none."""
        return self._topology.endpoint(name)

    def person_of(self, account: AccountKey) -> Person | None:
        return self._topology.person_of(account)

    def extension_type(self, endpoint: EndpointRef) -> str:
        """The type of the extension that serves the endpoint: `telegram`, `meshtastic`."""
        try:
            return self._extension_types[endpoint.instance]
        except KeyError:
            raise KeyError(f"no extension instance {endpoint.instance!r}") from None

    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]:
        """The recipients the endpoint reaches separately (the node ids of a Meshtastic `dm`
        endpoint), or `()` when it is one place."""
        return self._recipients.get(endpoint, ())

    def last_heard(
        self, account: AccountKey, endpoint: EndpointRef | None = None
    ) -> datetime | None:
        """When the hub last heard `account` (a message, or for a radio any packet), at
        `endpoint` or anywhere; `None` if it never did."""
        return self._history.last_heard(account, endpoint)

    def seen(self, fingerprint: Fingerprint, *, within: timedelta) -> bool:
        """Whether another message with this fingerprint arrived within `within` before now:
        the same message by another path (design.md §9.5)."""
        if within <= timedelta(0):
            raise ValueError("`within` must be a positive time span")
        return self._history.seen_since(fingerprint, self._now - within)
