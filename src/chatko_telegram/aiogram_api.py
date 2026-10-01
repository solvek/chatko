"""`TelegramApi` over aiogram 3: long polling and a few Bot API methods."""

import logging
from collections.abc import Awaitable, Sequence

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    AiogramError,
    ClientDecodeError,
    TelegramBadRequest,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import Chat as AiogramChat
from aiogram.types import (
    ChatMemberRestricted,
    ChatMemberUnion,
    ReactionTypeEmoji,
)
from aiogram.types import Message as AiogramMessage
from aiogram.types import Update as AiogramUpdate
from pydantic import ValidationError

from chatko.extension_api import Attachment, AttachmentKind
from chatko_telegram.api import (
    BotAdded,
    BotRefusedError,
    BotRemoved,
    Chat,
    ChatKind,
    ChatMessage,
    ChatMigrated,
    ChatUnavailableError,
    Event,
    RejectedError,
    Sender,
    TelegramApi,
    TelegramError,
    UnreachableError,
    Update,
)

ALLOWED_UPDATES = ["message", "my_chat_member"]
"""The updates the extension asks for: messages, and changes of the bot's own membership."""

REQUEST_MARGIN = 10
"""Seconds a long poll's HTTP request may take beyond the poll's own wait."""

_MEMBER_STATUSES = frozenset(
    {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}
)

# Content that has an attachment kind of its own, in the order it is checked.
_ATTACHMENTS: tuple[tuple[str, AttachmentKind], ...] = (
    ("photo", AttachmentKind.PHOTO),
    ("live_photo", AttachmentKind.PHOTO),
    ("video", AttachmentKind.VIDEO),
    ("animation", AttachmentKind.VIDEO),
    ("video_note", AttachmentKind.VIDEO),
    ("voice", AttachmentKind.VOICE),
    ("audio", AttachmentKind.AUDIO),
    ("document", AttachmentKind.FILE),
    ("sticker", AttachmentKind.STICKER),
    ("venue", AttachmentKind.LOCATION),
    ("location", AttachmentKind.LOCATION),
    ("contact", AttachmentKind.CONTACT),
    ("poll", AttachmentKind.POLL),
)
# Other content a person posts, which a text-only network shows as `[other]`. Every field of a
# message that is not here, in `_ATTACHMENTS` or text is a service message, which is not relayed.
_OTHER_CONTENT = ("checklist", "dice", "game", "giveaway", "invoice", "paid_media", "story")


class AiogramTelegramApi(TelegramApi):
    """The Bot API through an aiogram `Bot`. `session` replaces aiogram's HTTP session (tests)."""

    def __init__(
        self, token: str, logger: logging.Logger, *, session: BaseSession | None = None
    ) -> None:
        self._token = token
        self._logger = logger
        self._bot = Bot(token, session=session)

    async def get_updates(self, offset: int | None, wait: int) -> Sequence[Update]:
        try:
            updates = await self._bot.get_updates(
                offset=offset,
                timeout=wait,
                allowed_updates=ALLOWED_UPDATES,
                request_timeout=wait + REQUEST_MARGIN,
            )
        except ClientDecodeError as error:
            return self._read_one_by_one(error)
        except AiogramError as error:
            raise _translate(error, self._token) from error
        return [Update(update.update_id, _event(update)) for update in updates]

    def _read_one_by_one(self, error: ClientDecodeError) -> list[Update]:
        """The updates of an answer that aiogram could not read as a whole, e.g. because Telegram
        left out a field that aiogram requires. An update that cannot be read is skipped, as an
        update without an event; otherwise it would be handed over again and again."""
        data = error.data
        raw_updates = data.get("result") if isinstance(data, dict) and data.get("ok") else None
        if not isinstance(raw_updates, list):
            raise _translate(error, self._token) from error
        updates: list[Update] = []
        for raw in raw_updates:
            try:
                update = AiogramUpdate.model_validate(raw, context={"bot": self._bot})
            except ValidationError:
                update_id = raw.get("update_id") if isinstance(raw, dict) else None
                if not isinstance(update_id, int):
                    raise _translate(error, self._token) from error
                self._logger.warning(
                    "skipped Telegram update %d, which aiogram cannot read", update_id
                )
                updates.append(Update(update_id, None))
            else:
                updates.append(Update(update.update_id, _event(update)))
        return updates

    async def send_message(self, chat_id: int, text: str) -> int:
        message = await self._call(self._bot.send_message(chat_id, text, parse_mode=None))
        return message.message_id

    async def leave_chat(self, chat_id: int) -> None:
        await self._call(self._bot.leave_chat(chat_id))

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        await self._call(
            self._bot.set_message_reaction(
                chat_id, message_id, reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
        )

    async def close(self) -> None:
        await self._bot.session.close()

    async def _call[T](self, call: Awaitable[T]) -> T:
        try:
            return await call
        except AiogramError as error:
            raise _translate(error, self._token) from error


def _translate(error: AiogramError, token: str) -> TelegramError:
    """The port's error for an aiogram error, with the token taken out of its text."""
    reason = str(error).replace(token, "<token>")
    match error:
        case TelegramRetryAfter():
            return UnreachableError(reason, retry_after=error.retry_after)
        case TelegramMigrateToChat():
            return ChatUnavailableError(reason, migrated_to=error.migrate_to_chat_id)
    return _error_class(error, reason)(reason)


def _error_class(error: AiogramError, reason: str) -> type[TelegramError]:
    if isinstance(error, TelegramForbiddenError) or (
        isinstance(error, TelegramBadRequest) and "chat not found" in reason.lower()
    ):
        return ChatUnavailableError
    if isinstance(error, TelegramBadRequest):
        return RejectedError
    if isinstance(error, TelegramUnauthorizedError | TelegramConflictError | TelegramNotFound):
        return BotRefusedError
    # Network errors, Telegram's server errors, responses that cannot be read.
    return UnreachableError


def _event(update: AiogramUpdate) -> Event | None:
    if update.message is not None:
        return _message_event(update.message)
    if update.my_chat_member is not None:
        changed = update.my_chat_member
        was, now = _is_member(changed.old_chat_member), _is_member(changed.new_chat_member)
        if was == now:
            return None  # e.g. the bot was made an admin
        chat = _chat(changed.chat)
        if chat is None:
            return None
        return BotAdded(chat) if now else BotRemoved(chat)
    return None


def _message_event(message: AiogramMessage) -> Event | None:
    if message.migrate_to_chat_id is not None:
        return ChatMigrated(message.chat.id, message.migrate_to_chat_id)
    if message.migrate_from_chat_id is not None:
        return ChatMigrated(message.migrate_from_chat_id, message.chat.id)
    chat = _chat(message.chat)
    sender = _sender(message)
    if chat is None or sender is None:
        return None
    # One kind per message: the first that matches (a venue also carries its location).
    attachments = tuple(
        Attachment(kind) for field, kind in _ATTACHMENTS if getattr(message, field) is not None
    )[:1]
    if not attachments and any(getattr(message, field) is not None for field in _OTHER_CONTENT):
        attachments = (Attachment(AttachmentKind.OTHER),)
    text = message.text or message.caption or _content_text(message)
    if not text.strip() and not attachments:
        return None  # a service message: someone joined, a title changed, a pin
    return ChatMessage(chat, message.message_id, sender, text, attachments)


def _content_text(message: AiogramMessage) -> str:
    """The words that some content carries without a caption."""
    if message.sticker is not None:
        return message.sticker.emoji or ""
    if message.venue is not None:
        return message.venue.title
    if message.poll is not None:
        return message.poll.question
    return ""


def _chat(chat: AiogramChat) -> Chat | None:
    try:
        kind = ChatKind(chat.type)
    except ValueError:
        return None
    title = chat.title or _name(chat.first_name, chat.last_name)
    return Chat(chat.id, kind, title)


def _sender(message: AiogramMessage) -> Sender | None:
    if message.sender_chat is not None:  # an anonymous admin, a channel
        chat = message.sender_chat
        return Sender(chat.id, chat.title or "", chat.username)
    user = message.from_user
    if user is None:
        return None
    return Sender(user.id, _name(user.first_name, user.last_name), user.username)


def _name(first: str | None, last: str | None) -> str:
    return " ".join(part for part in (first, last) if part)


def _is_member(member: ChatMemberUnion) -> bool:
    if isinstance(member, ChatMemberRestricted):
        return member.is_member
    return member.status in _MEMBER_STATUSES
