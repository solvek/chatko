"""What `briarctl` knows of `briar-headless`: its records, its errors and the `BriarClient` port.

Ids are the URL-safe text of `briarctl.ids`, so a record can be printed, put into `chatko.yaml`
and given back to a command as it is. The records are the tool's own; nothing here is shared with
chatko or `chatko_briar` (design.md §7.5, D24).
"""

from dataclasses import dataclass
from typing import Protocol


class BriarError(Exception):
    """A failed call. `str(error)` is for the admin, and never holds the token."""


class UnreachableError(BriarError):
    """`briar-headless` did not answer, or failed."""


class RefusedError(BriarError):
    """The token is wrong (401)."""


class NotFoundError(BriarError):
    """The group, contact or invitation is not there (404)."""


class RejectedError(BriarError):
    """The request is refused. `code` is the API's `error`, e.g. `NOT_CREATOR`, if it sent one."""

    def __init__(self, reason: str, code: str | None = None) -> None:
        super().__init__(reason)
        self.code = code


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    """The name the contact gave itself."""
    alias: str | None
    author_id: str
    connected: bool
    verified: bool

    @property
    def shown(self) -> str:
        return self.alias or self.name


@dataclass(frozen=True)
class PendingContact:
    """A contact that is being added: the other side has not added the hub's link yet."""

    id: str
    alias: str
    state: str


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    creator: str
    """The creator's name."""
    ours: bool
    """The hub created the group."""
    dissolved: bool


@dataclass(frozen=True)
class Member:
    author_id: str
    name: str
    creator: bool
    contact_id: int | None
    """Set if the member is a contact of the hub."""
    visibility: str
    """`visible`, `invisible`, `revealed_by_us` or `revealed_by_contact`."""


@dataclass(frozen=True)
class Sharing:
    """Whether a contact can be invited to a group the hub created."""

    contact_id: int
    status: str
    """`shareable`, `invite_sent`, `sharing`, `not_supported` or `error`."""


@dataclass(frozen=True)
class Invitation:
    """An invitation to a group made by someone else."""

    group_id: str
    name: str
    creator: str
    contact_id: int


class BriarClient(Protocol):
    """The calls `briarctl` makes: `HttpBriarClient` is the real one, `FakeBriarClient` a fake."""

    def link(self) -> str: ...
    def contacts(self) -> list[Contact]: ...
    def pending_contacts(self) -> list[PendingContact]: ...
    def add_contact(self, link: str, alias: str) -> None: ...
    def remove_contact(self, contact_id: int) -> None: ...
    def cancel_pending(self, pending_id: str) -> None:
        """Stop adding a contact that has not completed (or failed), as the API names it."""
        ...

    def invitations(self) -> list[Invitation]: ...
    def answer_invitation(self, group_id: str, *, accept: bool) -> None: ...
    def groups(self) -> list[Group]: ...
    def create_group(self, name: str) -> Group: ...
    def leave_group(self, group_id: str) -> None:
        """Dissolve a group the hub created, or leave a group someone else created."""
        ...

    def members(self, group_id: str) -> list[Member]: ...
    def reveal(self, group_id: str, contact_id: int) -> None: ...
    def sharing(self, group_id: str) -> list[Sharing]: ...
    def invite(self, group_id: str, contact_id: int, text: str | None) -> Sharing: ...


_REASONS = {
    "NOT_CREATOR": "only the creator of a group can do that, and the hub did not create it",
    "NOT_MEMBER": "that contact has not joined the group",
    "DISSOLVED": "the group was dissolved by its creator",
    "INVALID_LINK": "that is not a valid briar:// link",
    "INVALID_PUBLIC_KEY": "the link's key is not valid",
    "CONTACT_EXISTS": "a contact with that link exists already",
    "PENDING_EXISTS": "a contact with that link is being added already",
    "NOT_SHAREABLE": "that contact cannot be invited",
}


def rejection(code: str | None, detail: str | None = None) -> RejectedError:
    """The error for a refusal with the API's `error` code, in words for the admin."""
    reason = _REASONS.get(code or "", f"briar-headless refused the request ({code or 'no reason'})")
    return RejectedError(f"{reason} ({detail})" if detail else reason, code)
