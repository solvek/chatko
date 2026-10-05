"""The Briar extension over the fake `briar-headless`: what it reads and posts, and what it
tells the admin (design.md §7)."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

import pytest

from chatko.extension_api import (
    Delivered,
    EndpointRef,
    Failed,
    InboundMessage,
    MessageId,
    OutboundMessage,
    Retry,
)
from chatko.extension_api.testing import FakeHub
from chatko_briar import BriarConfig, BriarExtension, BriarGroup, ids
from chatko_briar import extension as extension_module
from chatko_briar.api import (
    BriarError,
    DissolvedError,
    GroupUnavailableError,
    MessageAdded,
    MessageKind,
    RefusedError,
    RejectedError,
    UnreachableError,
)
from chatko_briar.extension import MAX_POST
from chatko_briar.testing import FakeBriarApi, make_id

CONFIG = BriarConfig.model_validate({"api": "http://briar:7000", "auth_token": "secret"})

FAMILY = EndpointRef("briar", "family.briar")
STREET = EndpointRef("briar", "street.briar")
FAMILY_GROUP = make_id("family")
STREET_GROUP = make_id("street")
ADA = make_id("Ada Lovelace")


def endpoints(*pairs: tuple[EndpointRef, bytes]) -> dict[EndpointRef, BriarGroup]:
    return {endpoint: BriarGroup(group=ids.to_text(group)) for endpoint, group in pairs}


def outbound(text: str = "Добрий вечір", label: str = "NatAda") -> OutboundMessage:
    return OutboundMessage(MessageId("m1"), label, text, FakeHub().now())


async def until(condition: Callable[[], bool], seconds: float = 2.0) -> None:
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110 - a plain predicate
            await asyncio.sleep(0.001)


class RefusingHub(FakeHub):
    """A hub that cannot take the first `refusals` messages."""

    def __init__(self, refusals: int) -> None:
        super().__init__()
        self.refusals = refusals

    async def submit(self, message: InboundMessage) -> None:
        if self.refusals:
            self.refusals -= 1
            raise RuntimeError("the database is busy")
        await super().submit(message)


@pytest.fixture
def api() -> FakeBriarApi:
    api = FakeBriarApi()
    api.add_group(FAMILY_GROUP, "Family")
    api.add_group(STREET_GROUP, "Street")
    return api


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


def make(api: FakeBriarApi, hub: FakeHub, *pairs: tuple[EndpointRef, bytes]) -> BriarExtension:
    extension = BriarExtension("briar", CONFIG, hub, api=api, backoff=(0.001, 0.01))
    extension.set_endpoints(endpoints(*(pairs or ((FAMILY, FAMILY_GROUP),))))
    return extension


@pytest.fixture
def extension(api: FakeBriarApi, hub: FakeHub) -> BriarExtension:
    return make(api, hub)


@pytest.fixture
async def running(extension: BriarExtension, api: FakeBriarApi) -> AsyncIterator[BriarExtension]:
    await extension.start()
    await api.idle()
    yield extension
    await extension.stop()


# Reading: events.


async def test_submits_a_post_with_its_author_and_marks_it_read(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    message = api.arrive(FAMILY_GROUP, "Привіт", name="Ada Lovelace")

    [submitted] = await hub.wait_for_submissions(1)
    await api.idle()

    assert submitted.endpoint == FAMILY
    assert submitted.transport_id == ids.to_text(message.id)
    assert submitted.text == "Привіт"
    assert str(submitted.author.key) == f"briar:{ids.to_text(ADA)}"
    assert submitted.author.display_name == "Ada Lovelace"
    assert api.unread(FAMILY_GROUP) == []


async def test_does_not_relay_joins(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.arrive(FAMILY_GROUP, "", name="Bob", kind=MessageKind.JOIN)
    api.arrive(FAMILY_GROUP, "marker")

    [submitted] = await hub.wait_for_submissions(1)
    await api.idle()

    assert submitted.text == "marker"


async def test_marks_a_blank_post_read_without_submitting_it(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.arrive(FAMILY_GROUP, "  \n")
    await api.idle()

    assert hub.submitted == []
    assert api.unread(FAMILY_GROUP) == []


async def test_a_post_of_the_overlap_is_handed_over_once(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    message = api.arrive(FAMILY_GROUP, "once")
    await hub.wait_for_submissions(1)
    await api.idle()

    api.push(MessageAdded(message))
    await api.idle()

    assert len(hub.submitted) == 1


async def test_a_failed_mark_as_read_is_not_a_failure(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    api.fail("mark_read", UnreachableError("slow"))
    with caplog.at_level(logging.INFO):
        api.arrive(FAMILY_GROUP, "one")
        api.arrive(FAMILY_GROUP, "two")
        await hub.wait_for_submissions(2)
        await api.idle()

    assert api.connections == 1
    assert api.unread(FAMILY_GROUP) == ["one"]
    assert "could not mark" in caplog.text


# Reading: the catch-up.


async def test_catches_up_with_what_was_posted_while_chatko_was_down(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.arrive(FAMILY_GROUP, "first", deliver=False)
    api.arrive(FAMILY_GROUP, "joined", kind=MessageKind.JOIN, deliver=False)
    api.arrive(FAMILY_GROUP, "mine", own=True, deliver=False)
    api.arrive(STREET_GROUP, "elsewhere", deliver=False)
    api.arrive(FAMILY_GROUP, "second", deliver=False)

    await extension.start()
    await hub.wait_for_submissions(2)
    await api.idle()
    await extension.stop()

    assert [message.text for message in hub.submitted] == ["first", "second"]
    assert api.unread(FAMILY_GROUP) == []
    assert api.unread(STREET_GROUP) == ["elsewhere"]


async def test_does_not_submit_a_post_that_is_marked_read(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    message = api.arrive(FAMILY_GROUP, "seen", deliver=False)
    await api.mark_read(FAMILY_GROUP, message.id)
    api.arrive(FAMILY_GROUP, "marker", deliver=False)

    await extension.start()
    await hub.wait_for_submissions(1)
    await api.idle()
    await extension.stop()

    assert [message.text for message in hub.submitted] == ["marker"]


async def test_catches_up_again_after_the_connection_is_lost(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.drop_connection()
    api.arrive(FAMILY_GROUP, "while away", deliver=False)

    [submitted] = await hub.wait_for_submissions(1)
    await api.idle()

    assert submitted.text == "while away"
    assert api.connections == 2


async def test_reconnects_with_a_backoff_while_briar_headless_is_down(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.fail("subscribe", *[UnreachableError("down")] * 3)
    api.arrive(FAMILY_GROUP, "waiting", deliver=False)

    await extension.start()
    [submitted] = await hub.wait_for_submissions(1)
    await extension.stop()

    assert submitted.text == "waiting"
    assert api.connections == 1


async def test_a_post_the_hub_could_not_take_is_handed_over_after_a_reconnect(
    api: FakeBriarApi,
) -> None:
    hub = RefusingHub(refusals=1)
    extension = make(api, hub)
    api.arrive(FAMILY_GROUP, "retry me", deliver=False)

    await extension.start()
    [submitted] = await hub.wait_for_submissions(1)
    await api.idle()
    await extension.stop()

    assert submitted.text == "retry me"
    assert api.connections == 2
    assert api.unread(FAMILY_GROUP) == []


async def test_a_new_endpoint_is_caught_up_while_running(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.arrive(STREET_GROUP, "before", deliver=False)

    running.set_endpoints(endpoints((FAMILY, FAMILY_GROUP), (STREET, STREET_GROUP)))
    [submitted] = await hub.wait_for_submissions(1)

    assert (submitted.endpoint, submitted.text) == (STREET, "before")


async def test_a_failed_catch_up_of_a_new_endpoint_is_logged(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    api.fail("groups", UnreachableError("slow"))
    with caplog.at_level(logging.WARNING):
        running.set_endpoints(endpoints((FAMILY, FAMILY_GROUP), (STREET, STREET_GROUP)))
        await until(lambda: "could not catch up" in caplog.text)


async def test_a_catch_up_of_a_new_endpoint_survives_a_surprise(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    api.fail("groups", RuntimeError("boom"))  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        running.set_endpoints(endpoints((FAMILY, FAMILY_GROUP), (STREET, STREET_GROUP)))
        await until(lambda: "could not catch up" in caplog.text)


async def test_a_group_the_hub_is_not_in_is_reported_once(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.leave(FAMILY_GROUP)

    await extension.start()
    await until(lambda: bool(hub.notices))
    await api.idle()
    await extension.stop()

    [notice] = hub.notices
    assert FAMILY.name in notice.text
    assert ids.to_text(FAMILY_GROUP) in notice.text
    assert "briarctl invitation accept" in notice.text


async def test_a_group_that_vanishes_during_the_catch_up_is_reported(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.fail("messages", GroupUnavailableError("gone"))

    await extension.start()
    await until(lambda: bool(hub.notices))
    await extension.stop()

    assert "not a member" in hub.notices[0].text


async def test_a_refused_token_is_reported_and_retried(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.fail("groups", RefusedError("rejects the auth token"))
    api.arrive(FAMILY_GROUP, "later", deliver=False)

    await extension.start()
    await hub.wait_for_submissions(1)
    await extension.stop()

    [notice] = hub.notices
    assert "auth_token" in notice.text
    assert "secret" not in notice.text


# Reading: dissolved groups.


async def test_tells_the_admin_when_a_group_is_dissolved(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.dissolve(FAMILY_GROUP)
    api.dissolve(FAMILY_GROUP)
    await api.idle()

    [notice] = hub.notices
    assert FAMILY.name in notice.text
    assert "dissolved" in notice.text
    assert ids.to_text(FAMILY_GROUP) in notice.text


async def test_ignores_the_dissolving_of_other_groups(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.dissolve(STREET_GROUP)
    await api.idle()

    assert hub.notices == []


async def test_a_group_found_dissolved_at_start_still_hands_over_its_unread_posts(
    extension: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.arrive(FAMILY_GROUP, "last words", deliver=False)
    api.dissolve(FAMILY_GROUP)

    await extension.start()
    await hub.wait_for_submissions(1)
    await extension.stop()

    assert len(hub.notices) == 1


async def test_posts_into_a_dissolved_group_fail(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.dissolve(FAMILY_GROUP)
    await api.idle()

    result = await running.deliver(FAMILY, outbound())

    assert isinstance(result, Failed)
    assert api.sent == []


async def test_a_dissolved_group_found_by_a_post_is_reported(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.fail("post", DissolvedError("dissolved"))

    result = await running.deliver(FAMILY, outbound())

    assert isinstance(result, Failed)
    assert len(hub.notices) == 1


# Endpoints.


def test_two_endpoints_cannot_be_the_same_group(extension: BriarExtension) -> None:
    with pytest.raises(ValueError, match="same group"):
        extension.set_endpoints(endpoints((FAMILY, FAMILY_GROUP), (STREET, FAMILY_GROUP)))


def test_has_no_recipients(extension: BriarExtension) -> None:
    assert extension.recipients(FAMILY) == ()


# Delivering.


async def test_posts_the_label_and_the_text(running: BriarExtension, api: FakeBriarApi) -> None:
    result = await running.deliver(FAMILY, outbound())

    assert result == Delivered(truncated=False)
    assert api.texts(FAMILY_GROUP) == ["NatAda: Добрий вечір"]


async def test_the_hubs_own_post_is_not_submitted_back(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    await running.deliver(FAMILY, outbound())
    api.arrive(FAMILY_GROUP, "marker")

    [submitted] = await hub.wait_for_submissions(1)

    assert submitted.text == "marker"


async def test_cuts_a_post_that_is_too_long(running: BriarExtension, api: FakeBriarApi) -> None:
    result = await running.deliver(FAMILY, outbound("я" * MAX_POST))

    [text] = api.texts(FAMILY_GROUP)
    assert result == Delivered(truncated=True)
    assert len(text.encode()) <= MAX_POST
    assert text.endswith("…")


async def test_does_not_cut_a_post_that_just_fits(
    running: BriarExtension, api: FakeBriarApi
) -> None:
    text = "x" * (MAX_POST - len("NatAda: "))

    assert await running.deliver(FAMILY, outbound(text)) == Delivered(truncated=False)


async def test_fails_for_an_endpoint_it_does_not_have(running: BriarExtension) -> None:
    assert isinstance(await running.deliver(STREET, outbound()), Failed)


async def test_fails_for_a_recipient(running: BriarExtension, api: FakeBriarApi) -> None:
    message = OutboundMessage(MessageId("m1"), "Ada", "hi", FakeHub().now(), recipient="x")

    assert isinstance(await running.deliver(FAMILY, message), Failed)
    assert api.sent == []


async def test_retries_before_it_started(extension: BriarExtension) -> None:
    assert isinstance(await extension.deliver(FAMILY, outbound()), Retry)


async def test_retries_while_briar_headless_is_down(
    running: BriarExtension, api: FakeBriarApi
) -> None:
    api.online = False

    result = await running.deliver(FAMILY, outbound())

    assert isinstance(result, Retry)
    assert result.after is None


async def test_retries_and_tells_the_admin_when_the_hub_is_not_in_the_group(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.leave(FAMILY_GROUP)

    assert isinstance(await running.deliver(FAMILY, outbound()), Retry)
    assert "briarctl invitation accept" in hub.notices[0].text


async def test_retries_and_tells_the_admin_when_the_token_is_refused(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.fail("post", RefusedError("rejects the auth token"))

    assert isinstance(await running.deliver(FAMILY, outbound()), Retry)
    assert "auth_token" in hub.notices[0].text


async def test_fails_when_briar_headless_rejects_the_request(
    running: BriarExtension, api: FakeBriarApi
) -> None:
    api.fail("post", RejectedError("bad request"))

    assert await running.deliver(FAMILY, outbound()) == Failed("bad request")


async def test_retries_after_any_other_briar_error(
    running: BriarExtension, api: FakeBriarApi
) -> None:
    api.fail("post", BriarError("odd"))

    assert await running.deliver(FAMILY, outbound()) == Retry("odd")


# Lifecycle.


async def test_stop_closes_the_api(extension: BriarExtension, api: FakeBriarApi) -> None:
    await extension.start()
    await extension.stop()
    await extension.stop()

    assert api.closed


async def test_start_connects_over_http_without_a_given_api(
    hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[tuple[str, str]] = []

    class Http(FakeBriarApi):
        def __init__(self, url: str, token: str, logger: logging.Logger) -> None:
            super().__init__()
            created.append((url, token))

    monkeypatch.setattr(extension_module, "HttpBriarApi", Http)
    extension = BriarExtension("briar", CONFIG, hub)

    await extension.start()
    await extension.stop()

    assert created == [("http://briar:7000", "secret")]


async def test_unexpected_errors_in_the_stream_reconnect(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub
) -> None:
    api.drop_connection()
    await until(lambda: api.connections == 2)
    api.arrive(FAMILY_GROUP, "after")

    [submitted] = await hub.wait_for_submissions(1)

    assert submitted.text == "after"


async def test_remembers_only_the_latest_handed_over_posts(
    running: BriarExtension, api: FakeBriarApi, hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension_module, "REMEMBERED_POSTS", 1)
    first = api.arrive(FAMILY_GROUP, "first")
    api.arrive(FAMILY_GROUP, "second")
    await hub.wait_for_submissions(2)
    await api.idle()

    api.push(MessageAdded(first))
    await api.idle()

    assert [message.text for message in hub.submitted] == ["first", "second", "first"]
