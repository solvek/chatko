"""The Telegram extension (design.md §5): groups and private chats with the bot as endpoints."""

from chatko_telegram.config import TelegramChat, TelegramConfig
from chatko_telegram.extension import TelegramExtension

__all__ = ["TelegramChat", "TelegramConfig", "TelegramExtension"]
