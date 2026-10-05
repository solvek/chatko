"""The HTTP adapter over a scripted `briar-headless`: what it asks and how it reads the answers."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from chatko_briar import ids
from chatko_briar.api import (
    DissolvedError,
    GroupDissolved,
    GroupMessage,
    GroupUnavailableError,
    MessageAdded,
    MessageKind,
    RefusedError,
    RejectedError,
    UnreachableError,
)
from chatko_briar.http_api import HttpBriarApi

TOKEN = "s3cret-token"
LOGGER = logging.getLogger("test")
GROUP = bytes(range(32))
AUTHOR = bytes(range(1, 33))
MESSAGE = bytes(range(2, 34))
SLASHY_GROUP = bytes([0xFB, 0xFF, 0xFE]) + bytes(29)

Handler = Callable[[httpx.Request], httpx.Response]


def post_json(
    *, own: bool = False, kind: str = "post", text: str | None = "Hello"
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "author": {"id": ids.to_json(AUTHOR), "name": "Alice", "formatVersion": 1},
        "authorStatus": "ourselves" if own else "unverified",
        "groupId": ids.to_json(GROUP),
        "id": ids.to_json(MESSAGE),
        "parentId": None,
        "read": own,
        "timestamp": 1537376633850,
        "type": kind,
    }
    if text is not None:
        item["text"] = text
    return item


def api_with(handler: Handler) -> HttpBriarApi:
    client = httpx.AsyncClient(
        base_url="http://briar:7000",
        headers={"Authorization": f"Bearer {TOKEN}"},
        transport=httpx.MockTransport(handler),
    )
    return HttpBriarApi("http://briar:7000", TOKEN, LOGGER, client=client)


def answering(status: int, body: Any = None) -> Handler:
    return lambda _: httpx.Response(status, json=body)


# REST.


async def test_lists_the_groups() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": ids.to_json(GROUP), "name": "Family", "dissolved": False, "ourGroup": True},
                {"id": ids.to_json(MESSAGE), "name": "Old", "dissolved": True, "ourGroup": False},
            ],
        )

    groups = await api_with(handler).groups()

    assert [(group.id, group.name, group.dissolved) for group in groups] == [
        (GROUP, "Family", False),
        (MESSAGE, "Old", True),
    ]
    assert seen[0].url.path == "/v1/groups"
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_lists_the_messages_of_a_group_by_its_url_safe_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=[post_json(), post_json(kind="join", text=None), post_json(own=True)]
        )

    posts = await api_with(handler).messages(SLASHY_GROUP)

    assert seen[0].url.path == f"/v1/groups/{ids.to_text(SLASHY_GROUP)}/messages"
    assert "/" not in ids.to_text(SLASHY_GROUP)
    assert posts == [
        GroupMessage(
            MESSAGE, GROUP, MessageKind.POST, AUTHOR, "Alice", False, False, "Hello", 1537376633850
        ),
        GroupMessage(
            MESSAGE, GROUP, MessageKind.JOIN, AUTHOR, "Alice", False, False, "", 1537376633850
        ),
        GroupMessage(
            MESSAGE, GROUP, MessageKind.POST, AUTHOR, "Alice", True, True, "Hello", 1537376633850
        ),
    ]


async def test_posts_a_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=post_json(own=True, text="NatAda: hi"))

    message = await api_with(handler).post(GROUP, "NatAda: hi")

    assert seen[0].method == "POST"
    assert seen[0].url.path == f"/v1/groups/{ids.to_text(GROUP)}/messages"
    assert json.loads(seen[0].content) == {"text": "NatAda: hi"}
    assert message.own


async def test_marks_a_message_read() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    await api_with(handler).mark_read(GROUP, MESSAGE)

    assert seen[0].url.path == f"/v1/groups/{ids.to_text(GROUP)}/messages/read"
    assert json.loads(seen[0].content) == {"messageId": ids.to_json(MESSAGE)}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, None, RefusedError),
        (404, None, GroupUnavailableError),
        (403, {"error": "DISSOLVED"}, DissolvedError),
        (403, {"error": "NOT_CREATOR"}, RejectedError),
        (403, ["not", "an", "object"], RejectedError),
        (403, {"error": 3}, RejectedError),
        (400, None, RejectedError),
        (500, None, UnreachableError),
        (503, None, UnreachableError),
        (429, None, UnreachableError),
    ],
)
async def test_maps_the_status_to_an_error(status: int, body: Any, error: type[Exception]) -> None:
    with pytest.raises(error) as raised:
        await api_with(answering(status, body)).post(GROUP, "x")

    assert TOKEN not in str(raised.value)


async def test_a_403_without_json_is_a_rejection() -> None:
    with pytest.raises(RejectedError):
        await api_with(lambda _: httpx.Response(403, text="no")).post(GROUP, "x")


async def test_a_network_failure_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect with {TOKEN}")

    with pytest.raises(UnreachableError) as raised:
        await api_with(handler).groups()

    assert TOKEN not in raised.value.reason


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.groups(),
        lambda api: api.messages(GROUP),
        lambda api: api.post(GROUP, "x"),
    ],
)
@pytest.mark.parametrize("body", [{"surprise": 1}, [{"id": "not base64!"}], "text", [1]])
async def test_an_unexpected_answer_is_a_rejection(
    call: Callable[[HttpBriarApi], Any], body: Any
) -> None:
    with pytest.raises(RejectedError, match="chatko does not know"):
        await call(api_with(answering(200, body)))


async def test_an_answer_that_is_not_json_is_a_rejection() -> None:
    with pytest.raises(RejectedError):
        await api_with(lambda _: httpx.Response(200, text="<html>")).groups()


# WebSocket.


class Server:
    """A `briar-headless` WebSocket that records what the client sends and sends what a test
    queues."""

    def __init__(self) -> None:
        self.received: list[str | bytes] = []
        self.to_send: list[str] = []
        self.hold_open = asyncio.Event()
        self.url = ""

    async def handle(self, connection: ServerConnection) -> None:
        self.received.append(await connection.recv())
        for frame in self.to_send:
            await connection.send(frame)
        await self.hold_open.wait()


@pytest.fixture
async def server() -> AsyncIterator[Server]:
    state = Server()
    async with serve(state.handle, "127.0.0.1", 0) as running:
        port = next(iter(running.sockets)).getsockname()[1]
        state.url = f"http://127.0.0.1:{port}"
        yield state
        state.hold_open.set()


def event(name: str, data: dict[str, Any]) -> str:
    return json.dumps({"name": name, "type": "event", "data": data})


async def test_authenticates_the_websocket_with_the_token_and_reads_events(
    server: Server,
) -> None:
    server.to_send = [
        json.dumps({"type": "other", "name": "GroupMessageAddedEvent", "data": {}}),
        "not json",
        event("ConversationMessageReceivedEvent", {"text": "private"}),
        event("GroupMessageAddedEvent", {"nonsense": True}),
        event("GroupMessageAddedEvent", post_json()),
        event("GroupDissolvedEvent", {"groupId": ids.to_json(GROUP)}),
    ]
    api = HttpBriarApi(server.url, TOKEN, LOGGER, timeout=2)

    stream = await api.subscribe()
    async with asyncio.timeout(2):
        added = await stream.next()
        dissolved = await stream.next()
    await api.close()

    assert server.received == [TOKEN]
    assert isinstance(added, MessageAdded)
    assert added.message.text == "Hello"
    assert dissolved == GroupDissolved(GROUP)


async def test_logs_a_malformed_event(server: Server, caplog: pytest.LogCaptureFixture) -> None:
    server.to_send = [
        event("GroupMessageAddedEvent", {"nonsense": True}),
        event("GroupDissolvedEvent", {"groupId": ids.to_json(GROUP)}),
    ]
    api = HttpBriarApi(server.url, TOKEN, LOGGER, timeout=2)

    with caplog.at_level(logging.WARNING):
        stream = await api.subscribe()
        async with asyncio.timeout(2):
            await stream.next()
    await api.close()

    assert "malformed" in caplog.text


async def test_a_lost_connection_is_unreachable(server: Server) -> None:
    api = HttpBriarApi(server.url, TOKEN, LOGGER, timeout=2)
    stream = await api.subscribe()
    server.hold_open.set()

    with pytest.raises(UnreachableError, match="lost"):
        async with asyncio.timeout(2):
            await stream.next()
    await stream.close()
    await api.close()


async def test_a_closed_stream_is_unreachable(server: Server) -> None:
    api = HttpBriarApi(server.url, TOKEN, LOGGER, timeout=2)
    stream = await api.subscribe()
    await stream.close()

    with pytest.raises(UnreachableError):
        await stream.next()
    await api.close()


async def test_close_closes_the_open_streams(server: Server) -> None:
    api = HttpBriarApi(server.url, TOKEN, LOGGER, timeout=2)
    stream = await api.subscribe()

    await api.close()

    with pytest.raises(UnreachableError):
        await stream.next()


async def test_cannot_connect_the_websocket_to_nothing() -> None:
    api = HttpBriarApi("http://127.0.0.1:1", TOKEN, LOGGER, timeout=1)

    with pytest.raises(UnreachableError) as raised:
        await api.subscribe()
    await api.close()

    assert TOKEN not in raised.value.reason


async def test_connects_to_https_as_wss() -> None:
    api = HttpBriarApi("https://127.0.0.1:1/", TOKEN, LOGGER, timeout=1)

    with pytest.raises(UnreachableError):
        await api.subscribe()
    await api.close()
