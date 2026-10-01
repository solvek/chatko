"""What exists in an installation: groups, sources and people (docs/design.md §2, §9)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from chatko.domain.accounts import Account, AccountKey, Author, Person
from chatko.domain.endpoints import EndpointRef, Group
from chatko.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class Topology:
    """The groups with their legs, the named sources, and the people of an installation.

    Every endpoint is either a leg of exactly one group or exactly one source; a person's label and
    each of their accounts belong to that person only.
    """

    groups: tuple[Group, ...] = ()
    sources: Mapping[str, EndpointRef] = field(default_factory=dict)
    people: tuple[Person, ...] = ()
    _group_by_name: Mapping[str, Group] = field(init=False, repr=False, compare=False)
    _group_by_leg: Mapping[EndpointRef, Group] = field(init=False, repr=False, compare=False)
    _person_by_account: Mapping[AccountKey, Person] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        object.__setattr__(self, "_group_by_name", _index_groups(self.groups))
        object.__setattr__(self, "_group_by_leg", _index_legs(self.groups))
        _check_sources(self.sources, self._group_by_leg)
        object.__setattr__(self, "_person_by_account", _index_people(self.people))

    @property
    def endpoints(self) -> frozenset[EndpointRef]:
        """Every leg and every source."""
        return frozenset(self._group_by_leg) | frozenset(self.sources.values())

    def has_endpoint(self, endpoint: EndpointRef) -> bool:
        return endpoint in self._group_by_leg or endpoint in self.sources.values()

    def group(self, name: str) -> Group:
        try:
            return self._group_by_name[name]
        except KeyError:
            raise KeyError(f"no group {name!r}") from None

    def group_of(self, endpoint: EndpointRef) -> Group | None:
        """The group the endpoint is a leg of; `None` for a source or an unknown endpoint."""
        return self._group_by_leg.get(endpoint)

    def source(self, name: str) -> EndpointRef:
        try:
            return self.sources[name]
        except KeyError:
            raise KeyError(f"no source {name!r}") from None

    def person_of(self, account: AccountKey) -> Person | None:
        return self._person_by_account.get(account)

    def author_of(self, account: Account) -> Author:
        """The author of a message posted by `account`, with the person if the config lists one."""
        return Author(account, self.person_of(account.key))


def _index_groups(groups: Iterable[Group]) -> Mapping[str, Group]:
    by_name: dict[str, Group] = {}
    for group in groups:
        if group.name in by_name:
            raise DomainError(f"group {group.name!r} is defined twice")
        by_name[group.name] = group
    return MappingProxyType(by_name)


def _index_legs(groups: Iterable[Group]) -> Mapping[EndpointRef, Group]:
    by_leg: dict[EndpointRef, Group] = {}
    for group in groups:
        for leg in group.legs:
            if leg in by_leg:
                raise DomainError(
                    f"endpoint {leg} is a leg of both {by_leg[leg].name!r} and {group.name!r}"
                )
            by_leg[leg] = group
    return MappingProxyType(by_leg)


def _check_sources(sources: Mapping[str, EndpointRef], legs: Mapping[EndpointRef, Group]) -> None:
    seen: dict[EndpointRef, str] = {}
    for name, endpoint in sources.items():
        if not name.strip():
            raise DomainError(f"source {endpoint} needs a name")
        if endpoint in legs:
            raise DomainError(
                f"source {name!r} is also a leg of group {legs[endpoint].name!r}: {endpoint}"
            )
        if endpoint in seen:
            raise DomainError(f"sources {seen[endpoint]!r} and {name!r} are the same {endpoint}")
        seen[endpoint] = name


def _index_people(people: Iterable[Person]) -> Mapping[AccountKey, Person]:
    labels: set[str] = set()
    by_account: dict[AccountKey, Person] = {}
    for person in people:
        if person.label in labels:
            raise DomainError(f"person {person.label!r} is defined twice")
        labels.add(person.label)
        for account in sorted(person.accounts):
            if account in by_account:
                raise DomainError(
                    f"account {account} belongs to both {by_account[account].label!r} "
                    f"and {person.label!r}"
                )
            by_account[account] = person
    return MappingProxyType(by_account)
