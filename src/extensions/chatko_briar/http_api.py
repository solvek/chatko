"""`HttpBriarApi`: the `BriarApi` port over `httpx` and `websockets` (design.md §7.4).

Every error it raises is a `BriarError` whose reason says what failed and never shows the token,
which travels only in the `Authorization` header and the WebSocket's first message.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from chatko_briar import ids
from chatko_briar.api import (
    BriarApi,
    BriarError,
    DissolvedError,
    Event,
    EventStream,
    Group,
    GroupDissolved,
    GroupMessage,
    GroupUnavailableError,
    MessageAdded,
    MessageKind,
    RefusedError,
    RejectedError,
    UnreachableError,
)

REQUEST_TIMEOUT = 30.0
"""Seconds that one request may take."""

_OWN = "ourselves"
"""`authorStatus` of the hub's own messages."""


class HttpBriarApi(BriarApi):
    def __init__(
        self,
        url: str,
        token: str,
        logger: logging.Logger,
        *,
        timeout: float = REQUEST_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._logger = logger
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(
            base_url=self._url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        )
        self._streams: set[_WebSocketStream] = set()

    async def subscribe(self) -> EventStream:
        socket_url = "ws" + self._url.removeprefix("http") + "/v1/ws"
        try:
            socket = await websockets.connect(socket_url, open_timeout=self._timeout)
            await socket.send(self._token)
        except (OSError, websockets.WebSocketException, TimeoutError) as error:
            raise UnreachableError(f"cannot connect the WebSocket: {_describe(error)}") from None
        stream = _WebSocketStream(socket, self._logger, self._streams)
        self._streams.add(stream)
        return stream

    async def groups(self) -> Sequence[Group]:
        body = await self._call("GET", "/v1/groups")
        try:
            return [
                Group(ids.from_json(item["id"]), item["name"], bool(item["dissolved"]))
                for item in body
            ]
        except (KeyError, TypeError, ValueError):
            raise _unexpected("the group list") from None

    async def messages(self, group_id: bytes) -> Sequence[GroupMessage]:
        body = await self._call("GET", f"/v1/groups/{ids.to_text(group_id)}/messages")
        try:
            return [_message(item) for item in body]
        except (KeyError, TypeError, ValueError):
            raise _unexpected("the message list") from None

    async def post(self, group_id: bytes, text: str) -> GroupMessage:
        body = await self._call(
            "POST", f"/v1/groups/{ids.to_text(group_id)}/messages", {"text": text}
        )
        try:
            return _message(body)
        except (KeyError, TypeError, ValueError):
            raise _unexpected("the new message") from None

    async def mark_read(self, group_id: bytes, message_id: bytes) -> None:
        await self._call(
            "POST",
            f"/v1/groups/{ids.to_text(group_id)}/messages/read",
            {"messageId": ids.to_json(message_id)},
        )

    async def close(self) -> None:
        for stream in list(self._streams):
            await stream.close()
        await self._client.aclose()

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - the JSON the API sends
        try:
            response = await self._client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise UnreachableError(f"briar-headless is unreachable: {_describe(error)}") from None
        status = response.status_code
        if status < 300:
            try:
                return response.json() if response.content else None
            except ValueError:
                raise _unexpected("the response") from None
        raise _failure(method, path, status, response)


class _WebSocketStream(EventStream):
    def __init__(
        self,
        socket: ClientConnection,
        logger: logging.Logger,
        registry: set["_WebSocketStream"],
    ) -> None:
        self._socket = socket
        self._logger = logger
        self._registry = registry

    async def next(self) -> Event:
        while True:
            try:
                frame = await self._socket.recv()
            except (websockets.WebSocketException, OSError) as error:
                raise UnreachableError(f"the WebSocket was lost: {_describe(error)}") from None
            event = self._parse(frame)
            if event is not None:
                return event

    def _parse(self, frame: str | bytes) -> Event | None:
        try:
            packet = json.loads(frame)
            if packet["type"] != "event":
                return None
            match packet["name"]:
                case "GroupMessageAddedEvent":
                    return MessageAdded(_message(packet["data"]))
                case "GroupDissolvedEvent":
                    return GroupDissolved(ids.from_json(packet["data"]["groupId"]))
        except (KeyError, TypeError, ValueError):
            self._logger.warning("ignored a malformed WebSocket event from briar-headless")
        return None

    async def close(self) -> None:
        self._registry.discard(self)
        await self._socket.close()


def _message(item: dict[str, Any]) -> GroupMessage:
    author = item["author"]
    return GroupMessage(
        id=ids.from_json(item["id"]),
        group_id=ids.from_json(item["groupId"]),
        kind=MessageKind(item["type"]),
        author_id=ids.from_json(author["id"]),
        author_name=str(author.get("name", "")),
        own=item.get("authorStatus") == _OWN,
        read=bool(item["read"]),
        text=str(item.get("text") or ""),
        timestamp=int(item.get("timestamp", 0)),
    )


def _failure(method: str, path: str, status: int, response: httpx.Response) -> BriarError:
    what = f"{method} {path} answered {status}"
    if status == 401:
        return RefusedError("briar-headless rejects the auth token")
    if status == 404:
        return GroupUnavailableError("the hub is not a member of that group")
    if status == 403 and _error_name(response) == "DISSOLVED":
        return DissolvedError("the group was dissolved by its creator")
    if status >= 500 or status == 429:
        return UnreachableError(f"briar-headless failed: {what}")
    return RejectedError(f"briar-headless refused the request: {what}")


def _error_name(response: httpx.Response) -> str | None:
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return None
    return error if isinstance(error, str) else None


def _unexpected(what: str) -> RejectedError:
    return RejectedError(f"briar-headless sent {what} in a form chatko does not know")


def _describe(error: Exception) -> str:
    return type(error).__name__
