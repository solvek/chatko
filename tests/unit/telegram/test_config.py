"""The config models of a Telegram instance and its endpoints."""

from typing import Any

import pytest
from pydantic import ValidationError

from chatko_telegram import TelegramChat, TelegramConfig


def test_takes_a_token_and_knows_the_bots_id_from_it() -> None:
    config = TelegramConfig.model_validate({"bot_token": "123456:AAH-x_9"})

    assert config.bot_id == 123456


@pytest.mark.parametrize("token", ["", "123456", "bot:secret", "123456:", "123 456:secret"])
def test_refuses_what_is_not_a_bot_token_and_never_says_it(token: str) -> None:
    with pytest.raises(ValidationError) as caught:
        TelegramConfig.model_validate({"bot_token": token})

    # The core reports the errors without their input (application/config.py).
    [problem] = caught.value.errors(include_input=False)
    assert "not a bot token" in problem["msg"]
    assert not token or token not in str(problem)


def test_hides_the_token_when_shown() -> None:
    config = TelegramConfig.model_validate({"bot_token": "123456:secret"})

    assert "secret" not in repr(config)


@pytest.mark.parametrize("chat", [-1001234567890, 123456789])
def test_takes_a_group_or_a_private_chat(chat: int) -> None:
    assert TelegramChat.model_validate({"chat": chat}).chat == chat


@pytest.mark.parametrize("chat", [0, "-1001234567890", True, 1.5, None])
def test_refuses_what_is_not_a_chat_id(chat: Any) -> None:
    with pytest.raises(ValidationError):
        TelegramChat.model_validate({"chat": chat})
