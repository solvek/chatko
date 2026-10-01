"""A fake of the `TelegramApi` port, for the extension's tests and its contract driver."""

import asyncio
import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from chatko.extension_api import Attachment
from chatko_telegram.api import (
    Chat,
    ChatMessage,
    Event,
    Sender,
    TelegramApi,
    TelegramError,
    UnreachableError,
    Update,
)


@dataclass(frozen=True, slots=True)
class SentMessage:
    chat_id: int
    text: str
    message_id: int


@dataclass(frozen=True, slots=True)
class Reaction:
    chat_id: int
    message_id: int
    emoji: str


class FakeTelegramApi(TelegramApi):
    """The Bot API in memory. A test makes things happen in Telegram (`post`, `push`), reads
    what the bot did (`sent`, `reactions`) and makes calls fail (`online`, `fail`).

    Like Telegram, `get_updates` hands over every update from its offset on, and a poll that asks
    with an offset confirms the updates before it.
    """

    def __init__(self) -> None:
        self.online = True
        self.closed = False
        self.sent: list[SentMessage] = []
        self.reactions: list[Reaction] = []
        self.offsets: list[int | None] = []
        """The offset of each poll, in order."""
        self._updates: list[Update] = []
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(1)
        self._failures: dict[str, list[TelegramError]] = {}
        self._arrived = asyncio.Event()
        self._idle = asyncio.Event()

    # What happens in Telegram.

    def post(
        self,
        chat: Chat,
        sender_id: int,
        text: str = "",
        *,
        name: str = "",
        username: str | None = None,
        attachments: Iterable[Attachment] = (),
    ) -> ChatMessage:
        """Someone posts in a chat; returns the message, which is also an update."""
        message = ChatMessage(
            chat,
            next(self._message_ids),
            Sender(sender_id, name, username),
            text,
            tuple(attachments),
        )
        self.push(message)
        return message

    def push(self, event: Event | None) -> Update:
        """Add an update; an event pushed before is handed over again as a new update."""
        update = Update(next(self._update_ids), event)
        self._updates.append(update)
        if not self.closed:
            self._idle.clear()
        self._arrived.set()
        return update

    def fail(self, method: str, *errors: TelegramError) -> None:
        """Make the next calls of `method` (`get_updates`, `send_message`, …) raise these."""
        self._failures.setdefault(method, []).extend(errors)

    def texts(self, chat_id: int) -> list[str]:
        """What the bot posted in a chat, oldest first."""
        return [message.text for message in self.sent if message.chat_id == chat_id]

    async def idle(self) -> None:
        """Wait until the bot has taken every update and polls again, or has closed the API."""
        await self._idle.wait()

    # The port.

    async def get_updates(self, offset: int | None, wait: int) -> Sequence[Update]:
        self._check("get_updates")
        self.offsets.append(offset)
        if offset is not None:
            self._updates = [update for update in self._updates if update.update_id >= offset]
        while not self._updates:
            self._arrived.clear()
            self._idle.set()
            try:
                async with asyncio.timeout(wait):
                    await self._arrived.wait()
            except TimeoutError:
                return []
        self._idle.clear()
        return list(self._updates)

    async def send_message(self, chat_id: int, text: str) -> int:
        self._check("send_message")
        message = SentMessage(chat_id, text, next(self._message_ids))
        self.sent.append(message)
        return message.message_id

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        self._check("set_reaction")
        self.reactions.append(Reaction(chat_id, message_id, emoji))

    async def close(self) -> None:
        self.closed = True
        self._idle.set()

    def _check(self, method: str) -> None:
        failures = self._failures.get(method)
        if failures:
            raise failures.pop(0)
        if not self.online:
            raise UnreachableError("Telegram is unreachable")
