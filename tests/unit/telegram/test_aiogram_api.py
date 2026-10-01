"""The aiogram adapter, over a scripted HTTP session: what it asks Telegram and how it reads the
answers."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import ClientDecodeError, TelegramNetworkError
from aiogram.methods import GetMe, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import ChatMemberAdministrator, ChatMemberRestricted, Poll
from pydantic import BaseModel

from chatko.extension_api import Attachment, AttachmentKind
from chatko_telegram.aiogram_api import ALLOWED_UPDATES, AiogramTelegramApi
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
    UnreachableError,
)

TOKEN = "123456:secret-token"
GROUP_ID = -1001234567890
GROUP = {"id": GROUP_ID, "type": "supergroup", "title": "Family"}
ADA = {"id": 111, "is_bot": False, "first_name": "Ada", "last_name": "Lovelace", "username": "ada"}
BOT = {"id": 123456, "is_bot": True, "first_name": "chatko", "username": "chatko_bot"}


class ScriptedSession(BaseSession):
    """Answers each request with the next scripted HTTP response, read by aiogram's own
    response check, and records the requests."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.responses: list[tuple[int, dict[str, Any]] | Exception] = []
        self.closed = False

    def answer(self, result: Any) -> None:
        self.responses.append((200, {"ok": True, "result": result}))

    def refuse(self, status: int, description: str, **parameters: Any) -> None:
        body = {"ok": False, "error_code": status, "description": description}
        if parameters:
            body["parameters"] = parameters
        self.responses.append((status, body))

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109 (aiogram's signature)
    ) -> TelegramType:
        self.requests.append(method)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, body = response
        return self.check_response(bot, method, status, json.dumps(body)).result  # type: ignore[return-value]

    async def close(self) -> None:
        self.closed = True

    async def stream_content(  # pragma: no cover
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        yield b""


@pytest.fixture
def session() -> ScriptedSession:
    return ScriptedSession()


@pytest.fixture
def api(session: ScriptedSession) -> AiogramTelegramApi:
    return AiogramTelegramApi(TOKEN, logging.getLogger("test"), session=session)


def message(**fields: Any) -> dict[str, Any]:
    return {"message_id": 7, "date": 1790000000, "chat": GROUP, "from": ADA, **fields}


def rights(model: type[BaseModel], **fields: Any) -> dict[str, Any]:
    """A member of the model's status with every right that the model requires off."""
    required = [name for name, field in model.model_fields.items() if field.is_required()]
    return {**dict.fromkeys(required, False), **fields}


async def event_of(
    api: AiogramTelegramApi, session: ScriptedSession, update: dict[str, Any]
) -> Event | None:
    session.answer([{"update_id": 5, **update}])
    [result] = await api.get_updates(None, 30)
    assert result.update_id == 5
    return result.event


# Asking.


async def test_polls_for_messages_and_the_bots_membership(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.answer([])

    assert await api.get_updates(42, 30) == []

    [request] = session.requests
    assert request.model_dump(include={"offset", "timeout", "allowed_updates"}) == {
        "offset": 42,
        "timeout": 30,
        "allowed_updates": ALLOWED_UPDATES,
    }
    assert ALLOWED_UPDATES == ["message", "my_chat_member"]


async def test_sends_plain_text_and_returns_the_message_id(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.answer(message(message_id=99, text="Ada: *hi*", **{"from": BOT}))

    assert await api.send_message(GROUP_ID, "Ada: *hi*") == 99

    [request] = session.requests
    assert request.model_dump(include={"chat_id", "text", "parse_mode"}) == {
        "chat_id": GROUP_ID,
        "text": "Ada: *hi*",
        "parse_mode": None,
    }


async def test_sets_a_reaction(api: AiogramTelegramApi, session: ScriptedSession) -> None:
    session.answer(True)

    await api.set_reaction(GROUP_ID, 7, "✍")

    [request] = session.requests
    assert request.model_dump(include={"chat_id", "message_id", "reaction"}) == {
        "chat_id": GROUP_ID,
        "message_id": 7,
        "reaction": [{"type": "emoji", "emoji": "✍"}],
    }


async def test_closes_the_session(api: AiogramTelegramApi, session: ScriptedSession) -> None:
    await api.close()

    assert session.closed


# Reading updates.


async def test_reads_a_text_message(api: AiogramTelegramApi, session: ScriptedSession) -> None:
    event = await event_of(api, session, {"message": message(text="Привіт")})

    assert event == ChatMessage(
        Chat(GROUP_ID, ChatKind.SUPERGROUP, "Family"),
        7,
        Sender(111, "Ada Lovelace", "ada"),
        "Привіт",
    )


async def test_reads_a_private_chat_with_the_persons_name(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    chat = {"id": 111, "type": "private", "first_name": "Ada"}
    event = await event_of(api, session, {"message": message(chat=chat, text="hi")})

    assert isinstance(event, ChatMessage)
    assert event.chat == Chat(111, ChatKind.PRIVATE, "Ada")


async def test_reads_a_post_of_a_chat_as_its_sender(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    # An anonymous admin posts as the group itself.
    event = await event_of(
        api, session, {"message": message(sender_chat=GROUP, text="from the admins")}
    )

    assert isinstance(event, ChatMessage)
    assert event.sender == Sender(GROUP_ID, "Family", None)


@pytest.mark.parametrize(
    ("content", "kind", "text"),
    [
        (
            {"photo": [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]},
            "photo",
            "",
        ),
        ({"voice": {"file_id": "f", "file_unique_id": "u", "duration": 3}}, "voice", ""),
        (
            {
                "sticker": {
                    "file_id": "f",
                    "file_unique_id": "u",
                    "type": "regular",
                    "width": 1,
                    "height": 1,
                    "is_animated": False,
                    "is_video": False,
                    "emoji": "👍",
                }
            },
            "sticker",
            "👍",
        ),
        (
            {
                "venue": {
                    "location": {"latitude": 50.45, "longitude": 30.52},
                    "title": "Base Camp",
                    "address": "Kyiv",
                },
                "location": {"latitude": 50.45, "longitude": 30.52},
            },
            "location",
            "Base Camp",
        ),
        (
            {
                "poll": rights(
                    Poll, id="p", question="Tea?", options=[], total_voter_count=0, type="regular"
                )
            },
            "poll",
            "Tea?",
        ),
        ({"dice": {"emoji": "🎲", "value": 3}}, "other", ""),
    ],
)
async def test_reads_content_as_one_attachment(
    api: AiogramTelegramApi,
    session: ScriptedSession,
    content: dict[str, Any],
    kind: str,
    text: str,
) -> None:
    event = await event_of(api, session, {"message": message(**content)})

    assert isinstance(event, ChatMessage)
    assert event.attachments == (Attachment(AttachmentKind(kind)),)
    assert event.text == text


async def test_reads_the_caption_of_an_attachment(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    document = {"file_id": "f", "file_unique_id": "u"}
    event = await event_of(
        api, session, {"message": message(document=document, caption="the plan")}
    )

    assert isinstance(event, ChatMessage)
    assert event.attachments == (Attachment(AttachmentKind.FILE),)
    assert event.text == "the plan"


async def test_skips_service_messages(api: AiogramTelegramApi, session: ScriptedSession) -> None:
    joined = message(new_chat_members=[ADA])

    assert await event_of(api, session, {"message": joined}) is None


async def test_skips_edits(api: AiogramTelegramApi, session: ScriptedSession) -> None:
    edited = message(text="fixed", edit_date=1790000100)

    assert await event_of(api, session, {"edited_message": edited}) is None


async def test_skips_chats_of_an_unknown_kind(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    chat = {**GROUP, "type": "something new"}

    assert await event_of(api, session, {"message": message(chat=chat, text="hi")}) is None


async def test_skips_a_message_without_a_sender(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    anonymous = {key: value for key, value in message(text="hi").items() if key != "from"}

    assert await event_of(api, session, {"message": anonymous}) is None


async def test_reads_a_migration_from_the_old_group(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    old = {"id": -123, "type": "group", "title": "Family"}
    moved = message(chat=old, migrate_to_chat_id=GROUP_ID)

    assert await event_of(api, session, {"message": moved}) == ChatMigrated(-123, GROUP_ID)


async def test_reads_a_migration_from_the_new_supergroup(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    moved = message(migrate_from_chat_id=-123)

    assert await event_of(api, session, {"message": moved}) == ChatMigrated(-123, GROUP_ID)


def membership(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {
        "my_chat_member": {
            "chat": GROUP,
            "from": ADA,
            "date": 1790000000,
            "old_chat_member": {"user": BOT, **old},
            "new_chat_member": {"user": BOT, **new},
        }
    }


ADMIN = rights(ChatMemberAdministrator, status="administrator")
del ADMIN["user"]


def restricted(*, is_member: bool) -> dict[str, Any]:
    member = rights(ChatMemberRestricted, status="restricted", is_member=is_member, until_date=0)
    del member["user"]
    return member


async def test_reads_that_the_bot_was_added(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    event = await event_of(api, session, membership({"status": "left"}, {"status": "member"}))

    assert event == BotAdded(Chat(GROUP_ID, ChatKind.SUPERGROUP, "Family"))


async def test_reads_that_the_bot_was_removed(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    kicked = {"status": "kicked", "until_date": 0}
    event = await event_of(api, session, membership({"status": "member"}, kicked))

    assert event == BotRemoved(Chat(GROUP_ID, ChatKind.SUPERGROUP, "Family"))


async def test_a_restricted_bot_is_a_member_while_it_is_in_the_chat(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    left = await event_of(
        api, session, membership(restricted(is_member=True), restricted(is_member=False))
    )

    assert isinstance(left, BotRemoved)


async def test_skips_a_change_of_the_bots_rights(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    assert await event_of(api, session, membership({"status": "member"}, ADMIN)) is None


async def test_skips_a_membership_change_in_a_chat_of_an_unknown_kind(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    update = membership({"status": "left"}, {"status": "member"})
    update["my_chat_member"]["chat"] = {**GROUP, "type": "something new"}

    assert await event_of(api, session, update) is None


# Errors.


async def test_flood_control_is_unreachable_with_its_wait(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.refuse(429, "Too Many Requests: retry after 7", retry_after=7)

    with pytest.raises(UnreachableError) as caught:
        await api.send_message(GROUP_ID, "hi")

    assert caught.value.retry_after == 7


async def test_a_group_that_became_a_supergroup_is_unavailable_with_the_new_id(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.refuse(400, "Bad Request: group chat was upgraded", migrate_to_chat_id=-100777)

    with pytest.raises(ChatUnavailableError) as caught:
        await api.send_message(-123, "hi")

    assert caught.value.migrated_to == -100777


@pytest.mark.parametrize(
    ("status", "description", "error"),
    [
        (403, "Forbidden: bot was kicked from the supergroup chat", ChatUnavailableError),
        (403, "Forbidden: bot can't initiate conversation with a user", ChatUnavailableError),
        (400, "Bad Request: chat not found", ChatUnavailableError),
        (400, "Bad Request: message text is empty", RejectedError),
        (401, "Unauthorized", BotRefusedError),
        (404, "Not Found", BotRefusedError),
        (409, "Conflict: terminated by other getUpdates request", BotRefusedError),
        (502, "Bad Gateway", UnreachableError),
        (418, "I'm a teapot", UnreachableError),
    ],
)
async def test_translates_telegrams_refusals(
    api: AiogramTelegramApi,
    session: ScriptedSession,
    status: int,
    description: str,
    error: type[Exception],
) -> None:
    session.refuse(status, description)

    with pytest.raises(error, match=description):
        await api.send_message(GROUP_ID, "hi")


async def test_a_network_error_is_unreachable_and_never_shows_the_token(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.responses.append(
        TelegramNetworkError(
            GetMe(),
            f"InvalidURL: https://api.telegram.org/bot{TOKEN}/sendMessage",
        )
    )

    with pytest.raises(UnreachableError) as caught:
        await api.send_message(GROUP_ID, "hi")

    assert TOKEN not in caught.value.reason
    assert "bot<token>/sendMessage" in caught.value.reason


async def test_an_answer_that_cannot_be_read_is_unreachable(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.answer({"not": "a message"})

    with pytest.raises(UnreachableError):
        await api.send_message(GROUP_ID, "hi")


# Updates that aiogram cannot read.


async def test_skips_an_update_that_aiogram_cannot_read_and_reads_the_others(
    api: AiogramTelegramApi, session: ScriptedSession, caplog: pytest.LogCaptureFixture
) -> None:
    unreadable = {"update_id": 5, "message": {"message_id": 6, "chat": GROUP}}  # no date
    session.answer([unreadable, {"update_id": 6, "message": message(text="hi")}])

    first, second = await api.get_updates(None, 30)

    assert first.update_id == 5
    assert first.event is None
    assert second.update_id == 6
    assert isinstance(second.event, ChatMessage)
    assert "skipped Telegram update 5" in caplog.text


async def test_an_unreadable_update_without_an_id_is_unreachable(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.answer([{"message": message(text="hi")}])

    with pytest.raises(UnreachableError):
        await api.get_updates(None, 30)


async def test_an_answer_that_is_not_json_is_unreachable(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.responses.append(ClientDecodeError("Failed to decode object", ValueError(), "<html>"))

    with pytest.raises(UnreachableError):
        await api.get_updates(None, 30)


async def test_a_poll_that_telegram_refuses_is_translated(
    api: AiogramTelegramApi, session: ScriptedSession
) -> None:
    session.refuse(409, "Conflict: terminated by other getUpdates request")

    with pytest.raises(BotRefusedError):
        await api.get_updates(None, 30)
