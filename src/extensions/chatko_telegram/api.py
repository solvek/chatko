"""The `TelegramApi` port: the part of the Bot API that the extension uses, in its own terms.

`AiogramTelegramApi` implements it over aiogram 3; `chatko_telegram.testing.FakeTelegramApi`
fakes it for the tests (docs/architecture.md §3.6).
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from chatko.extension_api import Attachment


class ChatKind(StrEnum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


@dataclass(frozen=True, slots=True)
class Chat:
    """A chat: a private chat with the bot (its id is the person's), a group or a channel."""

    id: int
    kind: ChatKind
    title: str = ""


@dataclass(frozen=True, slots=True)
class Sender:
    """Who posted a message: a user, or a chat that posts as itself (an anonymous group admin,
    a channel). `name` is the user's first and last name, or the chat's title."""

    id: int
    name: str = ""
    username: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A message with content: text, or an attachment with an optional caption in `text`."""

    chat: Chat
    message_id: int
    sender: Sender
    text: str = ""
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatMigrated:
    """A group became a supergroup, which has a new chat id."""

    old_chat_id: int
    new_chat_id: int


@dataclass(frozen=True, slots=True)
class BotAdded:
    """The bot became a member of the chat."""

    chat: Chat


@dataclass(frozen=True, slots=True)
class BotRemoved:
    """The bot is no longer a member of the chat."""

    chat: Chat


type Event = ChatMessage | ChatMigrated | BotAdded | BotRemoved


@dataclass(frozen=True, slots=True)
class Update:
    """One update from the Bot API. `event` is `None` for an update the extension has no use
    for (a service message, an edit); it still has to be confirmed."""

    update_id: int
    event: Event | None


class TelegramError(Exception):
    """A Bot API call failed. `reason` is safe to show the admin: it never contains the token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UnreachableError(TelegramError):
    """The network or Telegram is down or busy. Try again, after `retry_after` seconds if set."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


class BotRefusedError(TelegramError):
    """Telegram refuses the bot itself: a revoked token, or another program polls with it.
    Only the admin can fix it."""


class ChatUnavailableError(TelegramError):
    """The bot cannot use this chat now: it is not a member, the person has not pressed Start or
    blocked it, there is no such chat. If the group became a supergroup, `migrated_to` is the
    supergroup's id."""

    def __init__(self, reason: str, migrated_to: int | None = None) -> None:
        super().__init__(reason)
        self.migrated_to = migrated_to


class RejectedError(TelegramError):
    """Telegram refused the request itself: sending it again cannot help."""


class TelegramApi(ABC):
    """What the extension needs of the Bot API. Every method raises only `TelegramError`s."""

    @abstractmethod
    async def get_updates(self, offset: int | None, wait: int) -> Sequence[Update]:
        """The updates from `offset` on, oldest first, waiting up to `wait` seconds for one.

        Asking with an `offset` confirms every update before it: Telegram does not hand those
        over again. Updates not confirmed are handed over again, e.g. after a restart.
        """

    @abstractmethod
    async def send_message(self, chat_id: int, text: str) -> int:
        """Post a plain text (no markup) and return its message id."""

    @abstractmethod
    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        """Set the bot's reaction to a message."""

    @abstractmethod
    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""
