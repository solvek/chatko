"""The contract suite against the Telegram extension, over the fake Bot API."""

from typing import Any

from pydantic import BaseModel

from chatko.extension_api import Extension, HubContext
from chatko.extension_api.testing import ContractDriver, ExtensionContract
from chatko_telegram import TelegramConfig, TelegramExtension
from chatko_telegram.api import Chat, ChatKind, ChatMessage
from chatko_telegram.testing import FakeTelegramApi

BOT_ID = 123456
ADA = 111111111


def chat_id(place: int) -> int:
    return -1001000000000 - place


class TelegramDriver(ContractDriver[ChatMessage]):
    def __init__(self) -> None:
        self.api = FakeTelegramApi()

    @property
    def extension_class(self) -> type[Extension[Any]]:
        return TelegramExtension

    def config(self) -> dict[str, Any]:
        return {"bot_token": f"{BOT_ID}:contract-secret"}

    def endpoint_config(self, place: int) -> dict[str, Any]:
        return {"chat": chat_id(place)}

    def create(self, instance: str, config: BaseModel, hub: HubContext) -> Extension[Any]:
        assert isinstance(config, TelegramConfig)
        return TelegramExtension(
            instance, config, hub, api=self.api, poll_timeout=1, poll_backoff=(0.01, 0.05)
        )

    async def receive(
        self, place: int, text: str, *, by_hub: bool = False, recipient: str | None = None
    ) -> ChatMessage:
        chat = Chat(chat_id(place), ChatKind.SUPERGROUP, f"Group {place}")
        if by_hub:
            return self.api.post(chat, BOT_ID, text, name="chatko", username="chatko_bot")
        return self.api.post(chat, ADA, text, name="Ada Lovelace", username="ada")

    async def receive_again(self, post: ChatMessage) -> None:
        self.api.push(post)

    def posted(self, place: int) -> list[str]:
        return self.api.texts(chat_id(place))

    def go_offline(self) -> None:
        self.api.online = False

    async def settle(self) -> None:
        await super().settle()
        await self.api.idle()


class TestTelegramContract(ExtensionContract):
    def make_driver(self) -> ContractDriver[Any]:
        return TelegramDriver()
