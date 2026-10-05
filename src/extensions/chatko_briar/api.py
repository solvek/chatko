"""The `BriarApi` port: the part of the `briar-headless` API that the extension uses, in its own
terms, with ids as bytes (design.md §7.4).

`HttpBriarApi` implements it over `httpx` and `websockets`; `chatko_briar.testing.FakeBriarApi`
fakes it for the tests (docs/architecture.md §3.6).
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class MessageKind(StrEnum):
    JOIN = "join"
    POST = "post"


@dataclass(frozen=True, slots=True)
class Group:
    id: bytes
    name: str
    dissolved: bool = False


@dataclass(frozen=True, slots=True)
class GroupMessage:
    """A join or a post, as the API lists it. `own` is a message of the hub's account;
    `text` is empty for a join."""

    id: bytes
    group_id: bytes
    kind: MessageKind
    author_id: bytes
    author_name: str
    own: bool
    read: bool
    text: str = ""
    timestamp: int = 0


@dataclass(frozen=True, slots=True)
class MessageAdded:
    """Another member's join or post arrived. The API never sends the hub's own."""

    message: GroupMessage


@dataclass(frozen=True, slots=True)
class GroupDissolved:
    """The creator dissolved a group the hub joined, or the hub removed that creator."""

    group_id: bytes


type Event = MessageAdded | GroupDissolved


class BriarError(Exception):
    """A call failed. `reason` is safe to show the admin: it never contains the token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UnreachableError(BriarError):
    """`briar-headless` is down, restarting or too slow, or the connection was lost."""


class RefusedError(BriarError):
    """`briar-headless` rejects the token. Only the admin can fix it."""


class GroupUnavailableError(BriarError):
    """The hub has no such private group: the invitation is not accepted yet, or it left."""


class DissolvedError(BriarError):
    """The group's creator dissolved it, so nobody can post in it."""


class RejectedError(BriarError):
    """`briar-headless` refused the request itself: sending it again cannot help."""


class EventStream(ABC):
    """The authenticated WebSocket of `briar-headless`."""

    @abstractmethod
    async def next(self) -> Event:
        """The next event, waiting for it. Events the extension has no use for are skipped.

        Raises `UnreachableError` when the connection is lost: events are lost with it, which is
        why the extension catches up after every new connection.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""


class BriarApi(ABC):
    """What the extension needs of `briar-headless`. Every method raises only `BriarError`s."""

    @abstractmethod
    async def subscribe(self) -> EventStream:
        """Connect the WebSocket and authenticate it, so that no event is missed from now on."""

    @abstractmethod
    async def groups(self) -> Sequence[Group]:
        """The private groups the hub is a member of."""

    @abstractmethod
    async def messages(self, group_id: bytes) -> Sequence[GroupMessage]:
        """The joins and posts of a group, oldest first.

        Raises `GroupUnavailableError` for a group the hub is not a member of.
        """

    @abstractmethod
    async def post(self, group_id: bytes, text: str) -> GroupMessage:
        """Post as the hub. Raises `GroupUnavailableError`, `DissolvedError`, and
        `RejectedError` for a text over 31 744 bytes."""

    @abstractmethod
    async def mark_read(self, group_id: bytes, message_id: bytes) -> None:
        """Mark a message read: the extension's note that the hub has stored it."""

    @abstractmethod
    async def close(self) -> None:
        """Close every connection. Safe to call more than once."""
