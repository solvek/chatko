"""The config of a Telegram instance and of its endpoints (config.example.yaml)."""

import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")


class TelegramConfig(BaseModel):
    """`bot_token` is the token from BotFather, through an environment variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_token: SecretStr

    @field_validator("bot_token")
    @classmethod
    def _is_a_token(cls, token: SecretStr) -> SecretStr:
        if not _TOKEN.fullmatch(token.get_secret_value()):
            raise ValueError("not a bot token from BotFather (<bot id>:<secret>)")
        return token

    @property
    def bot_id(self) -> int:
        """The bot's own user id, the part of the token before the colon."""
        return int(self.bot_token.get_secret_value().partition(":")[0])


class TelegramChat(BaseModel):
    """An endpoint: a chat by id. A group's id is negative; a private chat's is the person's id,
    and the person must have pressed Start in the chat with the bot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chat: int = Field(strict=True)

    @field_validator("chat")
    @classmethod
    def _is_a_chat(cls, chat: int) -> int:
        if chat == 0:
            raise ValueError("a chat id is not 0")
        return chat
