"""The Telegram extension over the fake Bot API: what it reads and posts, and what it
tells the admin."""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from chatko.extension_api import (
    Attachment,
    AttachmentKind,
    Delivered,
    DeliveryReport,
    EndpointRef,
    Failed,
    InboundMessage,
    MessageId,
    OutboundMessage,
    Retry,
)
from chatko.extension_api.testing import FakeHub
from chatko_telegram import TelegramChat, TelegramConfig, TelegramExtension
from chatko_telegram import extension as extension_module
from chatko_telegram.api import (
    BotAdded,
    BotRefusedError,
    BotRemoved,
    Chat,
    ChatKind,
    ChatMigrated,
    ChatUnavailableError,
    RejectedError,
    UnreachableError,
)
from chatko_telegram.extension import MAX_TEXT, TRUNCATED_REACTION
from chatko_telegram.testing import FakeTelegramApi, Reaction

BOT = 123456
ADA = 111111111
CONFIG = TelegramConfig.model_validate({"bot_token": f"{BOT}:secret"})

FAMILY = EndpointRef("tg", "family.tg")
STREET = EndpointRef("tg", "street.tg")
OWNER = EndpointRef("tg", "owner")
FAMILY_CHAT = Chat(-1001000000001, ChatKind.SUPERGROUP, "Family")
STREET_CHAT = Chat(-1001000000002, ChatKind.SUPERGROUP, "Street")
OWNER_CHAT = Chat(ADA, ChatKind.PRIVATE, "Ada")
FOREIGN = Chat(-1009999999999, ChatKind.SUPERGROUP, "Strangers")
OLD_FAMILY = Chat(-4001, ChatKind.GROUP, "Family")
SUPERGROUP_ID = -1004001


def endpoints(**chats: int) -> dict[EndpointRef, TelegramChat]:
    names = {"family": FAMILY, "street": STREET, "owner": OWNER}
    return {names[name]: TelegramChat(chat=chat) for name, chat in chats.items()}


DEFAULT_ENDPOINTS = endpoints(family=FAMILY_CHAT.id, owner=OWNER_CHAT.id)


@pytest.fixture
def api() -> FakeTelegramApi:
    return FakeTelegramApi()


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


@pytest.fixture
def extension(api: FakeTelegramApi, hub: FakeHub) -> TelegramExtension:
    extension = TelegramExtension(
        "tg", CONFIG, hub, api=api, poll_timeout=1, poll_backoff=(0.001, 0.01)
    )
    extension.set_endpoints(DEFAULT_ENDPOINTS)
    return extension


@pytest.fixture
async def running(extension: TelegramExtension) -> AsyncIterator[TelegramExtension]:
    await extension.start()
    yield extension
    await extension.stop()


def outbound(
    text: str = "Добрий вечір",
    *,
    attachments: tuple[Attachment, ...] = (),
    recipient: str | None = None,
) -> OutboundMessage:
    return OutboundMessage(
        MessageId("m1"), "Ada", text, FakeHub().now(), attachments, recipient=recipient
    )


async def settle(api: FakeTelegramApi) -> None:
    async with asyncio.timeout(2):
        await api.idle()


# Endpoints.


def test_refuses_two_endpoints_in_one_chat(extension: TelegramExtension) -> None:
    with pytest.raises(ValueError, match="same chat"):
        extension.set_endpoints(endpoints(family=FAMILY_CHAT.id, street=FAMILY_CHAT.id))


# Reading.


@pytest.mark.usefixtures("running")
async def test_submits_a_message_with_its_author(api: FakeTelegramApi, hub: FakeHub) -> None:
    posted = api.post(FAMILY_CHAT, ADA, "Привіт", name="Наталія Адамчук", username="natada")

    [message] = await hub.wait_for_submissions(1)

    assert message.endpoint == FAMILY
    assert message.transport_id == str(posted.message_id)
    assert message.text == "Привіт"
    assert message.author.key.kind == "telegram"
    assert message.author.key.external_id == str(ADA)
    assert message.author.display_name == "Наталія Адамчук"
    assert message.author.short_name == "natada"
    assert message.from_recipient is None


@pytest.mark.usefixtures("running")
async def test_submits_attachments_with_their_caption(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.post(FAMILY_CHAT, ADA, "the river", attachments=[Attachment(AttachmentKind.PHOTO)])

    [message] = await hub.wait_for_submissions(1)

    assert message.attachments == (Attachment(AttachmentKind.PHOTO),)
    assert message.text == "the river"


@pytest.mark.usefixtures("running")
async def test_reads_a_private_chat_that_is_an_endpoint(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.post(OWNER_CHAT, ADA, "a note to the feed")

    [message] = await hub.wait_for_submissions(1)

    assert message.endpoint == OWNER


@pytest.mark.parametrize("start", ["/start", "/start@chatko_bot", "/start family"])
@pytest.mark.usefixtures("running")
async def test_drops_the_start_command_in_a_private_chat(
    api: FakeTelegramApi, hub: FakeHub, start: str
) -> None:
    api.post(OWNER_CHAT, ADA, start)
    api.post(OWNER_CHAT, ADA, "/started a fire")

    await hub.wait_for_submissions(1)
    await settle(api)

    assert [message.text for message in hub.submitted] == ["/started a fire"]


@pytest.mark.usefixtures("running")
async def test_relays_the_start_command_in_a_group(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.post(FAMILY_CHAT, ADA, "/start")

    [message] = await hub.wait_for_submissions(1)

    assert message.text == "/start"


@pytest.mark.usefixtures("running")
async def test_ignores_private_chats_that_are_not_endpoints(
    api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.post(Chat(222, ChatKind.PRIVATE, "Bob"), 222, "hello bot")
    await settle(api)

    assert hub.submitted == []
    assert hub.notices == []


@pytest.mark.usefixtures("running")
async def test_ignores_a_group_that_is_not_an_endpoint_and_tells_the_admin(
    api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.post(FOREIGN, ADA, "hello")
    await settle(api)

    assert hub.submitted == []
    [notice] = hub.notices
    assert str(FOREIGN.id) in notice.text
    assert "Strangers" in notice.text
    assert notice.key == f"unknown:{FOREIGN.id}"


@pytest.mark.usefixtures("running")
async def test_tells_the_admin_of_a_foreign_group_once(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.post(FOREIGN, ADA, "hello")
    api.post(FOREIGN, ADA, "anyone?")
    await settle(api)

    assert len(hub.notices) == 1


async def test_tells_the_admin_again_after_a_new_config(
    running: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.post(FOREIGN, ADA, "hello")
    await settle(api)
    running.set_endpoints(DEFAULT_ENDPOINTS)
    api.post(FOREIGN, ADA, "anyone?")
    await settle(api)

    assert len(hub.notices) == 2


@pytest.mark.usefixtures("running")
async def test_confirms_the_updates_it_has_handled(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.push(None)  # an update without use, e.g. an edit
    api.post(FAMILY_CHAT, ADA, "one")
    await hub.wait_for_submissions(1)
    await settle(api)

    assert api.offsets[0] is None
    assert api.offsets[-1] == 3


# Joining and being removed.


@pytest.mark.usefixtures("running")
async def test_stays_in_a_group_it_is_added_to_and_only_logs_it(
    api: FakeTelegramApi, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    api.push(BotAdded(FOREIGN))
    await settle(api)

    assert hub.notices == []
    assert f"added to the supergroup 'Strangers' (chat id {FOREIGN.id})" in caplog.text


@pytest.mark.usefixtures("running")
async def test_stays_in_a_chat_that_is_an_endpoint(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.push(BotAdded(FAMILY_CHAT))
    api.push(BotAdded(Chat(222, ChatKind.PRIVATE, "Bob")))  # Bob pressed Start
    await settle(api)

    assert hub.notices == []


@pytest.mark.parametrize(("chat", "how"), [(FAMILY_CHAT, "removed"), (OWNER_CHAT, "blocked")])
@pytest.mark.usefixtures("running")
async def test_tells_the_admin_when_an_endpoint_removes_the_bot(
    api: FakeTelegramApi, hub: FakeHub, chat: Chat, how: str
) -> None:
    api.push(BotRemoved(chat))
    await settle(api)

    [notice] = hub.notices
    assert f"{how} the bot" in notice.text
    assert notice.key == f"removed:{chat.id}"


@pytest.mark.usefixtures("running")
async def test_does_not_mind_leaving_a_foreign_chat(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.push(BotRemoved(FOREIGN))
    await settle(api)

    assert hub.notices == []


# A group that becomes a supergroup.


@pytest.fixture
async def migrated(
    extension: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> AsyncIterator[TelegramExtension]:
    extension.set_endpoints(endpoints(family=OLD_FAMILY.id, owner=OWNER_CHAT.id))
    await extension.start()
    api.push(ChatMigrated(OLD_FAMILY.id, SUPERGROUP_ID))
    api.push(ChatMigrated(OLD_FAMILY.id, SUPERGROUP_ID))  # also told in the new supergroup
    await settle(api)
    yield extension
    await extension.stop()


async def test_follows_a_group_that_became_a_supergroup(
    migrated: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.post(Chat(SUPERGROUP_ID, ChatKind.SUPERGROUP, "Family"), ADA, "still here")
    [message] = await hub.wait_for_submissions(1)
    result = await migrated.deliver(FAMILY, outbound())

    assert message.endpoint == FAMILY
    assert result == Delivered()
    assert api.texts(SUPERGROUP_ID) == ["Ada: Добрий вечір"]


@pytest.mark.usefixtures("migrated")
async def test_asks_the_admin_to_put_the_supergroups_id_into_the_config(hub: FakeHub) -> None:
    [notice] = hub.notices

    assert f"replace {OLD_FAMILY.id} with {SUPERGROUP_ID}" in notice.text
    assert notice.key == f"migrated:{OLD_FAMILY.id}"


async def test_keeps_following_the_supergroup_with_the_same_endpoints(
    migrated: TelegramExtension, api: FakeTelegramApi
) -> None:
    migrated.set_endpoints(endpoints(family=OLD_FAMILY.id, owner=OWNER_CHAT.id))

    await migrated.deliver(FAMILY, outbound())

    assert api.texts(SUPERGROUP_ID) == ["Ada: Добрий вечір"]


async def test_takes_the_supergroups_id_from_the_config(
    migrated: TelegramExtension, api: FakeTelegramApi
) -> None:
    migrated.set_endpoints(endpoints(family=SUPERGROUP_ID))

    await migrated.deliver(FAMILY, outbound())

    assert api.texts(SUPERGROUP_ID) == ["Ada: Добрий вечір"]


async def test_follows_a_group_added_to_its_supergroup_before_the_migration(
    extension: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    # The order Telegram reports it in (S18, D45): the bot in the new supergroup first.
    extension.set_endpoints(endpoints(family=OLD_FAMILY.id))
    await extension.start()
    supergroup = Chat(SUPERGROUP_ID, ChatKind.SUPERGROUP, "Family")
    api.push(BotAdded(supergroup))
    api.push(ChatMigrated(OLD_FAMILY.id, SUPERGROUP_ID))  # told in the new supergroup
    api.push(ChatMigrated(OLD_FAMILY.id, SUPERGROUP_ID))  # and in the old group
    api.push(BotAdded(supergroup))  # made an admin
    api.post(supergroup, ADA, "still here")
    [message] = await hub.wait_for_submissions(1)
    await extension.stop()

    assert message.endpoint == FAMILY
    [notice] = hub.notices
    assert notice.key == f"migrated:{OLD_FAMILY.id}"


@pytest.mark.usefixtures("running")
async def test_ignores_a_foreign_group_that_became_a_supergroup(
    api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.push(ChatMigrated(-5, -1005))
    await settle(api)

    assert hub.notices == []


async def test_follows_a_migration_that_a_delivery_reveals(
    extension: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    extension.set_endpoints(endpoints(family=OLD_FAMILY.id))
    await extension.start()
    api.fail("send_message", ChatUnavailableError("upgraded", migrated_to=SUPERGROUP_ID))

    result = await extension.deliver(FAMILY, outbound())
    await extension.stop()

    assert result == Delivered()
    assert api.texts(SUPERGROUP_ID) == ["Ada: Добрий вечір"]
    assert [notice.key for notice in hub.notices] == [f"migrated:{OLD_FAMILY.id}"]


async def test_does_not_post_into_a_supergroup_of_another_endpoint(
    extension: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    extension.set_endpoints(endpoints(family=OLD_FAMILY.id, street=SUPERGROUP_ID))
    await extension.start()
    api.fail("send_message", ChatUnavailableError("upgraded", migrated_to=SUPERGROUP_ID))

    result = await extension.deliver(FAMILY, outbound())
    await extension.stop()

    assert isinstance(result, Retry)
    assert api.sent == []
    assert [notice.key for notice in hub.notices] == ["cannot-post:family.tg"]


# Delivering.


async def test_posts_the_label_and_the_text(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    attachments = (Attachment(AttachmentKind.PHOTO),)

    result = await running.deliver(FAMILY, outbound("the river", attachments=attachments))

    assert result == Delivered(truncated=False)
    assert api.texts(FAMILY_CHAT.id) == ["Ada: [photo] the river"]


async def test_posts_a_text_of_the_longest_length_whole(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    text = "я" * (MAX_TEXT - len("Ada: "))

    result = await running.deliver(FAMILY, outbound(text))

    assert result == Delivered(truncated=False)
    assert api.texts(FAMILY_CHAT.id) == [f"Ada: {text}"]


async def test_cuts_a_longer_text_and_says_so(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    result = await running.deliver(FAMILY, outbound("я" * MAX_TEXT))

    [posted] = api.texts(FAMILY_CHAT.id)
    assert result == Delivered(truncated=True)
    assert len(posted) == MAX_TEXT
    assert posted.endswith("я…")


async def test_counts_the_length_as_telegram_does_and_never_splits_a_character(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    # An emoji outside the BMP is two UTF-16 units; the cut falls in the middle of one.
    await running.deliver(FAMILY, outbound("x" + "🦊" * MAX_TEXT))

    [posted] = api.texts(FAMILY_CHAT.id)
    assert len(posted.encode("utf-16-le")) // 2 <= MAX_TEXT
    assert posted.endswith("🦊…")


async def test_fails_for_an_endpoint_it_does_not_have(running: TelegramExtension) -> None:
    result = await running.deliver(STREET, outbound())

    assert isinstance(result, Failed)


async def test_fails_for_a_recipient(running: TelegramExtension) -> None:
    result = await running.deliver(FAMILY, outbound(recipient="!a1b2c3d4"))

    assert isinstance(result, Failed)


async def test_retries_while_it_is_not_running(extension: TelegramExtension) -> None:
    result = await extension.deliver(FAMILY, outbound())

    assert isinstance(result, Retry)


async def test_retries_after_the_wait_telegram_asks_for(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    api.fail("send_message", UnreachableError("flood control", retry_after=7))

    result = await running.deliver(FAMILY, outbound())

    assert result == Retry("flood control", timedelta(seconds=7))


async def test_retries_with_the_hubs_backoff_while_telegram_is_unreachable(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    api.fail("send_message", UnreachableError("timeout"))

    result = await running.deliver(FAMILY, outbound())

    assert result == Retry("timeout")


async def test_retries_and_tells_the_admin_when_it_cannot_post_in_a_chat(
    running: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.fail("send_message", ChatUnavailableError("Forbidden: bot was kicked"))

    result = await running.deliver(FAMILY, outbound())

    assert result == Retry("Forbidden: bot was kicked")
    [notice] = hub.notices
    assert "family.tg" in notice.text
    assert "Forbidden: bot was kicked" in notice.text


async def test_retries_and_tells_the_admin_when_telegram_refuses_the_bot(
    running: TelegramExtension, api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.fail("send_message", BotRefusedError("Unauthorized"))

    result = await running.deliver(FAMILY, outbound())

    assert result == Retry("Unauthorized")
    assert [notice.key for notice in hub.notices] == ["bot-refused"]


async def test_fails_what_telegram_rejects(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    api.fail("send_message", RejectedError("Bad Request: message text is empty"))

    result = await running.deliver(FAMILY, outbound())

    assert result == Failed("Bad Request: message text is empty")


# Delivery reports.


def report(result: Delivered | Failed, *, source: EndpointRef = FAMILY) -> DeliveryReport:
    return DeliveryReport(source, "42", EndpointRef("kyiv", "family.radio"), None, result)


async def test_marks_a_message_that_was_cut_short_elsewhere(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    await running.delivery_report(report(Delivered(truncated=True)))

    assert api.reactions == [Reaction(FAMILY_CHAT.id, 42, TRUNCATED_REACTION)]


@pytest.mark.parametrize("result", [Delivered(), Failed("gave up")])
async def test_leaves_other_messages_alone(
    running: TelegramExtension, api: FakeTelegramApi, result: Delivered | Failed
) -> None:
    await running.delivery_report(report(result))

    assert api.reactions == []


async def test_ignores_a_report_about_an_endpoint_it_no_longer_has(
    running: TelegramExtension, api: FakeTelegramApi
) -> None:
    await running.delivery_report(report(Delivered(truncated=True), source=STREET))

    assert api.reactions == []


async def test_a_reaction_that_fails_is_only_logged(
    running: TelegramExtension, api: FakeTelegramApi, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    api.fail("set_reaction", RejectedError("REACTION_INVALID"))

    await running.delivery_report(report(Delivered(truncated=True)))

    assert "REACTION_INVALID" in caplog.text


# Polling.


@pytest.mark.usefixtures("running")
async def test_polls_again_after_a_failure(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.fail("get_updates", UnreachableError("timeout"), UnreachableError("timeout"))
    api.post(FAMILY_CHAT, ADA, "after the outage")

    [message] = await hub.wait_for_submissions(1)

    assert message.text == "after the outage"


@pytest.mark.usefixtures("running")
async def test_waits_as_long_as_telegram_asks(api: FakeTelegramApi, hub: FakeHub) -> None:
    api.fail("get_updates", UnreachableError("flood control", retry_after=0.01))
    api.post(FAMILY_CHAT, ADA, "later")

    [message] = await hub.wait_for_submissions(1)

    assert message.text == "later"


@pytest.mark.usefixtures("running")
async def test_tells_the_admin_when_telegram_refuses_the_bot_while_polling(
    api: FakeTelegramApi, hub: FakeHub
) -> None:
    api.fail("get_updates", BotRefusedError("Conflict: terminated by other getUpdates request"))
    api.post(FAMILY_CHAT, ADA, "marker")
    await hub.wait_for_submissions(1)

    [notice] = hub.notices
    assert "Conflict" in notice.text
    assert notice.key == "bot-refused"


class FlakyHub(FakeHub):
    """Cannot store the first message it is handed."""

    def __init__(self) -> None:
        super().__init__()
        self.refused = 0

    async def submit(self, message: InboundMessage) -> None:
        if not self.refused:
            self.refused += 1
            raise RuntimeError("the database is locked")
        await super().submit(message)


async def test_hands_a_message_over_again_when_the_hub_could_not_take_it(
    api: FakeTelegramApi,
) -> None:
    hub = FlakyHub()
    extension = TelegramExtension(
        "tg", CONFIG, hub, api=api, poll_timeout=1, poll_backoff=(0.001, 0.01)
    )
    extension.set_endpoints(DEFAULT_ENDPOINTS)
    await extension.start()
    posted = api.post(FAMILY_CHAT, ADA, "keep me")

    [message] = await hub.wait_for_submissions(1)
    await extension.stop()

    assert hub.refused == 1
    assert message.transport_id == str(posted.message_id)


# Lifecycle.


async def test_closes_the_api_when_it_stops(
    extension: TelegramExtension, api: FakeTelegramApi
) -> None:
    await extension.start()
    await extension.stop()

    assert api.closed


async def test_connects_through_aiogram_without_a_given_api(
    hub: FakeHub, api: FakeTelegramApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens: list[str] = []

    def connect(token: str, logger: object) -> FakeTelegramApi:
        tokens.append(token)
        return api

    monkeypatch.setattr(extension_module, "AiogramTelegramApi", connect)
    extension = TelegramExtension("tg", CONFIG, hub)
    await extension.start()
    await extension.stop()

    assert tokens == [f"{BOT}:secret"]
    assert api.closed


async def test_the_fake_api_answers_an_empty_poll_when_its_wait_is_over() -> None:
    assert await FakeTelegramApi().get_updates(None, 0) == []
