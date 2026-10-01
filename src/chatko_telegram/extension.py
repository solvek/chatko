"""The Telegram extension: groups and private chats with the bot as endpoints (design.md §5)."""

import asyncio
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from chatko.extension_api import (
    Account,
    AccountKey,
    Delivered,
    DeliveryReport,
    DeliveryResult,
    EndpointProvider,
    EndpointRef,
    Extension,
    Failed,
    HubContext,
    InboundMessage,
    OutboundMessage,
    Retry,
)
from chatko_telegram.aiogram_api import AiogramTelegramApi
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
    TelegramApi,
    TelegramError,
    UnreachableError,
)
from chatko_telegram.config import TelegramChat, TelegramConfig

MAX_TEXT = 4096
"""The longest text message, in UTF-16 code units as Telegram counts them."""

TRUNCATED_REACTION = "✍"
"""The bot's reaction to a message that another network got only part of (design.md §6.3).
Bots can react only with Telegram's standard reactions, which have no ✂️."""

POLL_TIMEOUT = 30
"""Seconds a long poll waits for an update."""

POLL_BACKOFF = (1.0, 60.0)
"""Seconds to wait after a failed poll: the first wait, doubling up to the longest."""

_START = re.compile(r"/start(@\w+)?(\s.*)?", re.DOTALL)


class TelegramExtension(Extension[TelegramConfig], EndpointProvider[TelegramChat]):
    """Reads and posts in the Telegram chats of its endpoints, through one bot.

    It serves only those chats: it leaves any other group or channel it is added to and tells
    the admin the chat id, so the admin can add the chat to the config (design.md §5). When a
    group becomes a supergroup, which changes its chat id, it follows the group until the hub
    restarts and asks the admin to change the config.

    `api` replaces the Bot API (tests); without it, `start` connects through aiogram.
    """

    type_name: ClassVar[str] = "telegram"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = TelegramConfig
    endpoint_config_model: ClassVar[type[BaseModel]] = TelegramChat

    def __init__(
        self,
        instance: str,
        config: TelegramConfig,
        hub: HubContext,
        *,
        api: TelegramApi | None = None,
        poll_timeout: int = POLL_TIMEOUT,
        poll_backoff: tuple[float, float] = POLL_BACKOFF,
    ) -> None:
        super().__init__(instance, config, hub)
        self._given_api = api
        self._poll_timeout = poll_timeout
        self._poll_backoff = poll_backoff
        self._api: TelegramApi | None = None
        self._poller: asyncio.Task[None] | None = None
        self._chats: dict[EndpointRef, int] = {}
        """Each endpoint's chat id, as in the config."""
        self._moved: dict[int, int] = {}
        """Configured chat ids of groups that became supergroups, and the supergroups' ids."""
        self._by_chat: dict[int, EndpointRef] = {}
        """The endpoint of each chat the bot serves now."""

    # Endpoints.

    def set_endpoints(self, endpoints: Mapping[EndpointRef, TelegramChat]) -> None:
        chats: dict[EndpointRef, int] = {}
        owners: dict[int, EndpointRef] = {}
        for endpoint, config in endpoints.items():
            if config.chat in owners:
                raise ValueError(f"{endpoint} and {owners[config.chat]} are the same chat")
            owners[config.chat] = endpoint
            chats[endpoint] = config.chat
        self._chats = chats
        self._index()

    def _index(self) -> None:
        by_chat = {chat: endpoint for endpoint, chat in self._chats.items()}
        for old, new in self._moved.items():
            endpoint = by_chat.get(old)
            if endpoint is not None and new not in by_chat:
                del by_chat[old]
                by_chat[new] = endpoint
        self._by_chat = by_chat

    def _chat_of(self, endpoint: EndpointRef) -> int | None:
        """The chat the endpoint is now, or `None` if it is not an endpoint of this instance."""
        for chat, owner in self._by_chat.items():
            if owner == endpoint:
                return chat
        return None

    # Lifecycle.

    async def start(self) -> None:
        if self._given_api is not None:
            self._api = self._given_api
        else:
            self._api = AiogramTelegramApi(self.config.bot_token.get_secret_value(), self.logger)
        self._poller = asyncio.create_task(
            self._poll(self._api), name=f"chatko.telegram.{self.instance}"
        )
        self.logger.info("polling Telegram as bot %d", self.config.bot_id)

    async def stop(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            await asyncio.wait({self._poller})
            self._poller = None
        if self._api is not None:
            await self._api.close()
            self._api = None

    # Reading.

    async def _poll(self, api: TelegramApi) -> None:
        offset: int | None = None
        failures = 0
        while True:
            try:
                updates = await api.get_updates(offset, self._poll_timeout)
                for update in updates:
                    if update.event is not None:
                        await self._handle(api, update.event)
                    offset = update.update_id + 1
            except TelegramError as error:
                failures += 1
                await self._poll_failed(error, failures)
            except Exception:
                # The hub could not take a message: the update stays unconfirmed, so the next
                # poll hands it over again.
                failures += 1
                self.logger.exception("could not hand over a Telegram update")
                await asyncio.sleep(self._backoff(failures))
            else:
                failures = 0

    async def _poll_failed(self, error: TelegramError, failures: int) -> None:
        self.logger.warning("polling Telegram failed: %s", error.reason)
        if isinstance(error, BotRefusedError):
            await self._bot_refused(error)
        if isinstance(error, UnreachableError) and error.retry_after is not None:
            await asyncio.sleep(error.retry_after)
        else:
            await asyncio.sleep(self._backoff(failures))

    def _backoff(self, failures: int) -> float:
        first, longest = self._poll_backoff
        return min(first * 2.0 ** min(failures - 1, 32), longest)

    async def _handle(self, api: TelegramApi, event: Event) -> None:
        match event:
            case ChatMessage():
                await self._on_message(event)
            case ChatMigrated():
                await self._on_migrated(event.old_chat_id, event.new_chat_id)
            case BotAdded():
                await self._on_added(api, event.chat)
            case BotRemoved():
                await self._on_removed(event.chat)

    async def _on_message(self, message: ChatMessage) -> None:
        if message.sender.id == self.config.bot_id:
            return  # the hub's own post
        endpoint = self._by_chat.get(message.chat.id)
        if endpoint is None:
            await self._on_foreign_message(message.chat)
            return
        if message.chat.kind is ChatKind.PRIVATE and _START.fullmatch(message.text):
            return  # what the Telegram app sends when the person presses Start
        sender = message.sender
        author = Account(AccountKey(self.type_name, str(sender.id)), sender.name, sender.username)
        await self.hub.submit(
            InboundMessage(
                endpoint, str(message.message_id), author, message.text, message.attachments
            )
        )

    async def _on_foreign_message(self, chat: Chat) -> None:
        if chat.kind is ChatKind.PRIVATE:
            self.logger.info(
                "ignored a private message in chat %d, which is not an endpoint", chat.id
            )
            return
        await self.hub.notify_admin(
            f"The bot of {self.instance} is in the Telegram {chat.kind} {chat.title!r} "
            f"(chat id {chat.id}), which is not in the config, and ignores its messages. Add "
            "the chat to the config, or remove the bot from it.",
            key=f"unknown:{chat.id}",
        )

    async def _on_added(self, api: TelegramApi, chat: Chat) -> None:
        if chat.id in self._by_chat or chat.kind is ChatKind.PRIVATE:
            return
        try:
            await api.leave_chat(chat.id)
        except TelegramError as error:
            outcome = f"could not leave it ({error.reason})"
        else:
            outcome = "left it"
        self.logger.info("added to chat %d, which is not an endpoint: %s", chat.id, outcome)
        await self.hub.notify_admin(
            f"The bot of {self.instance} was added to the Telegram {chat.kind} {chat.title!r} "
            f"(chat id {chat.id}), which is not in the config, and {outcome}. To serve the "
            "chat, add it to the config and add the bot again.",
            key=f"foreign:{chat.id}",
        )

    async def _on_removed(self, chat: Chat) -> None:
        endpoint = self._by_chat.get(chat.id)
        if endpoint is None:
            return
        how = "blocked the bot" if chat.kind is ChatKind.PRIVATE else "removed the bot"
        await self.hub.notify_admin(
            f"{endpoint}: the Telegram chat {chat.id} {how}. Messages for it wait until the "
            "bot is back.",
            key=f"removed:{chat.id}",
        )

    async def _on_migrated(self, old: int, new: int) -> None:
        endpoint = self._by_chat.get(old)
        if endpoint is None or new in self._by_chat:
            return  # not ours, or followed already
        self._moved[self._chats[endpoint]] = new
        self._index()
        await self.hub.notify_admin(
            f"{endpoint}: the Telegram group became a supergroup with the chat id {new}. The "
            f"bot follows it until the hub restarts: replace {self._chats[endpoint]} with {new} "
            "in the config.",
            key=f"migrated:{self._chats[endpoint]}",
        )

    # Delivering.

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        chat = self._chat_of(endpoint)
        if chat is None:
            return Failed(f"{endpoint} is not an endpoint of {self.instance}")
        if message.recipient is not None:
            return Failed(f"{endpoint} has no recipients, so not {message.recipient!r}")
        api = self._api
        if api is None:
            return Retry(f"{self.instance} is not running")
        text, truncated = _fit(message.formatted)
        try:
            await self._send(api, endpoint, chat, text)
        except TelegramError as error:
            return await self._not_delivered(endpoint, error)
        return Delivered(truncated=truncated)

    async def _send(self, api: TelegramApi, endpoint: EndpointRef, chat: int, text: str) -> None:
        try:
            await api.send_message(chat, text)
        except ChatUnavailableError as error:
            if error.migrated_to is None:
                raise
            await self._on_migrated(chat, error.migrated_to)
            moved = self._chat_of(endpoint)
            if moved is None or moved == chat:
                raise  # another endpoint has the supergroup
            await api.send_message(moved, text)

    async def _not_delivered(self, endpoint: EndpointRef, error: TelegramError) -> DeliveryResult:
        match error:
            case UnreachableError(retry_after=float() | int() as seconds):
                return Retry(error.reason, timedelta(seconds=seconds))
            case ChatUnavailableError():
                await self.hub.notify_admin(
                    f"{endpoint}: the bot cannot post in its Telegram chat: {error.reason}. "
                    "Messages for it wait until it can.",
                    key=f"cannot-post:{endpoint.name}",
                )
            case BotRefusedError():
                await self._bot_refused(error)
            case RejectedError():
                return Failed(error.reason)
        return Retry(error.reason)

    async def delivery_report(self, report: DeliveryReport) -> None:
        if not (isinstance(report.result, Delivered) and report.result.truncated):
            return
        chat = self._chat_of(report.source)
        api = self._api
        if chat is None or api is None or not report.transport_id.isdigit():
            return
        try:
            await api.set_reaction(chat, int(report.transport_id), TRUNCATED_REACTION)
        except TelegramError as error:
            self.logger.info(
                "could not mark message %s of %s as cut short: %s",
                report.transport_id,
                report.source,
                error.reason,
            )

    async def _bot_refused(self, error: BotRefusedError) -> None:
        await self.hub.notify_admin(
            f"Telegram refuses the bot of {self.instance}: {error.reason}. Check its token, and "
            "that no other program uses it.",
            key="bot-refused",
        )


def _fit(text: str) -> tuple[str, bool]:
    """The text cut to `MAX_TEXT` with `…` at the end, and whether it was cut."""
    encoded = text.encode("utf-16-le")
    if len(encoded) <= 2 * MAX_TEXT:
        return text, False
    # Half a surrogate pair at the cut is dropped.
    return encoded[: 2 * (MAX_TEXT - 1)].decode("utf-16-le", errors="ignore") + "…", True
