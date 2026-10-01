"""Accounts, people and authors (docs/design.md §2, §8)."""

import re
from dataclasses import dataclass

from chatko.domain.errors import DomainError

_KIND = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True, slots=True, order=True)
class AccountKey:
    """An author's identity in some network: `telegram:123`, `meshtastic:!a1b2c3d4`, `briar:<id>`.

    `kind` names the network (the extension type, not the instance), so the same person has the same
    key on every instance of an extension. The core gives `kind` no meaning beyond that.
    """

    kind: str
    external_id: str

    def __post_init__(self) -> None:
        if not _KIND.fullmatch(self.kind):
            raise DomainError(f"invalid account kind {self.kind!r}")
        if not self.external_id or self.external_id != self.external_id.strip():
            raise DomainError(f"invalid {self.kind} account id {self.external_id!r}")

    @classmethod
    def parse(cls, text: str) -> "AccountKey":
        """Parse the `kind:external_id` form used in the config."""
        kind, sep, external_id = text.partition(":")
        if not sep:
            raise DomainError(f"an account is written as kind:id, got {text!r}")
        return cls(kind, external_id)

    def __str__(self) -> str:
        return f"{self.kind}:{self.external_id}"


@dataclass(frozen=True, slots=True)
class Account:
    """An account as a network shows it at the moment: its key and the names the network gives.

    `short_name` is a network's own short form of the name, if it has one (a Meshtastic node's
    short name). Either name may be empty, e.g. before a Meshtastic node's NodeInfo is heard.
    """

    key: AccountKey
    display_name: str = ""
    short_name: str | None = None


@dataclass(frozen=True, slots=True)
class Person:
    """A human listed in the config: one label for all their accounts (docs/design.md §8)."""

    label: str
    accounts: frozenset[AccountKey]

    def __post_init__(self) -> None:
        if not self.label.strip() or self.label != self.label.strip():
            raise DomainError(f"invalid person label {self.label!r}")
        if any(not char.isprintable() for char in self.label):
            raise DomainError(f"person label {self.label!r} contains control characters")
        if not self.accounts:
            raise DomainError(f"person {self.label!r} has no accounts")


@dataclass(frozen=True, slots=True)
class Author:
    """Who wrote a message, as far as the hub knows.

    `account` is the account that posted it. `person` is set when the config lists that account.
    `relayed_label` is set when a peer hub relayed the message (docs/design.md §9.6): `account` is
    then the peer's, and the original author is known only by the label the peer put in front of
    the text.
    """

    account: Account
    person: Person | None = None
    relayed_label: str | None = None

    def __post_init__(self) -> None:
        if self.person is not None and self.account.key not in self.person.accounts:
            raise DomainError(f"{self.account.key} is not an account of {self.person.label!r}")
        if self.relayed_label is not None and not self.relayed_label.strip():
            raise DomainError("a relayed author needs a label")
