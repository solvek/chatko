"""A fake of the `BriarApi` port, for the extension's tests and its contract driver."""

import asyncio
import dataclasses
import hashlib
from collections.abc import Sequence

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
    RejectedError,
    UnreachableError,
)
from chatko_briar.extension import MAX_POST


def make_id(label: str) -> bytes:
    """A stable 32-byte id for a name, e.g. a group or an author."""
    return hashlib.sha256(label.encode()).digest()


class FakeBriarApi(BriarApi):
    """`briar-headless` in memory. A test makes things happen in Briar (`add_group`, `arrive`,
    `dissolve`, `drop_connection`), reads what the hub did (`sent`, `texts`) and makes calls fail
    (`online`, `fail`).

    Like the real one, it hands an event only to a connected WebSocket, never sends the hub's own
    posts, and keeps the read flag of each message.
    """

    def __init__(self) -> None:
        self.online = True
        self.closed = False
        self.connections = 0
        """How many WebSockets were opened."""
        self.sent: list[GroupMessage] = []
        """The hub's posts, oldest first."""
        self._groups: dict[bytes, Group] = {}
        self._log: dict[bytes, list[GroupMessage]] = {}
        self._streams: list[_FakeStream] = []
        self._failures: dict[str, list[BriarError]] = {}
        self._counter = 0
        self._idle = asyncio.Event()

    # What happens in Briar.

    def add_group(self, group_id: bytes, name: str = "Group") -> None:
        """The hub is a member of a group."""
        self._groups[group_id] = Group(group_id, name)
        self._log.setdefault(group_id, [])

    def arrive(
        self,
        group_id: bytes,
        text: str,
        *,
        author: bytes | None = None,
        name: str = "Ada Lovelace",
        own: bool = False,
        kind: MessageKind = MessageKind.POST,
        deliver: bool = True,
    ) -> GroupMessage:
        """Someone posts in a group; the hub's own post (`own`) is listed but never pushed.
        With `deliver` false the WebSocket does not hear of it, as when chatko is down."""
        message = self._new(group_id, text, author or make_id(name), name, own, kind)
        if deliver and not own:
            self.push(MessageAdded(message))
        return message

    def push(self, event: Event) -> None:
        """Send an event to every connected WebSocket."""
        for stream in self._streams:
            stream.queue.put_nowait(event)
        if self._streams:
            self._idle.clear()

    def dissolve(self, group_id: bytes) -> None:
        """The group's creator dissolves it."""
        self._groups[group_id] = dataclasses.replace(self._groups[group_id], dissolved=True)
        self.push(GroupDissolved(group_id))

    def leave(self, group_id: bytes) -> None:
        """The hub is no longer a member, e.g. its invitation was never accepted."""
        self._groups.pop(group_id, None)

    def drop_connection(self) -> None:
        """The WebSockets break; the extension has to reconnect."""
        for stream in self._streams:
            stream.queue.put_nowait(UnreachableError("the WebSocket was lost"))

    def fail(self, method: str, *errors: BriarError) -> None:
        """Make the next calls of `method` (`subscribe`, `groups`, `messages`, `post`,
        `mark_read`) raise these."""
        self._failures.setdefault(method, []).extend(errors)

    def texts(self, group_id: bytes) -> list[str]:
        """What the hub posted in a group, oldest first."""
        return [message.text for message in self.sent if message.group_id == group_id]

    def unread(self, group_id: bytes) -> list[str]:
        """The texts of the posts of others that are not marked read."""
        return [
            message.text
            for message in self._log.get(group_id, [])
            if message.kind is MessageKind.POST and not message.read
        ]

    async def idle(self) -> None:
        """Wait until the extension has taken every event and waits for the next one, or has
        closed the API."""
        await self._idle.wait()

    # The port.

    async def subscribe(self) -> EventStream:
        self._check("subscribe")
        self.connections += 1
        stream = _FakeStream(self)
        self._streams.append(stream)
        return stream

    async def groups(self) -> Sequence[Group]:
        self._check("groups")
        return list(self._groups.values())

    async def messages(self, group_id: bytes) -> Sequence[GroupMessage]:
        self._check("messages")
        self._member(group_id)
        return list(self._log[group_id])

    async def post(self, group_id: bytes, text: str) -> GroupMessage:
        self._check("post")
        group = self._member(group_id)
        if group.dissolved:
            raise DissolvedError("the group was dissolved")
        if len(text.encode()) > MAX_POST:
            raise RejectedError("the text is too long")
        message = self._new(group_id, text, make_id("hub"), "chatko", True, MessageKind.POST)
        self.sent.append(message)
        return message

    async def mark_read(self, group_id: bytes, message_id: bytes) -> None:
        self._check("mark_read")
        self._member(group_id)
        log = self._log[group_id]
        for index, message in enumerate(log):
            if message.id == message_id:
                log[index] = dataclasses.replace(message, read=True)

    async def close(self) -> None:
        self.closed = True
        self._idle.set()
        for stream in list(self._streams):
            await stream.close()

    # Inside.

    def _new(
        self,
        group_id: bytes,
        text: str,
        author: bytes,
        name: str,
        own: bool,
        kind: MessageKind,
    ) -> GroupMessage:
        self._counter += 1
        message = GroupMessage(
            id=make_id(f"message {self._counter}"),
            group_id=group_id,
            kind=kind,
            author_id=author,
            author_name=name,
            own=own,
            read=own,
            text=text if kind is MessageKind.POST else "",
            timestamp=self._counter,
        )
        self._log.setdefault(group_id, []).append(message)
        return message

    def _member(self, group_id: bytes) -> Group:
        group = self._groups.get(group_id)
        if group is None:
            raise GroupUnavailableError("not a member of that group")
        return group

    def _check(self, method: str) -> None:
        failures = self._failures.get(method)
        if failures:
            raise failures.pop(0)
        if not self.online:
            raise UnreachableError("briar-headless is unreachable")


class _FakeStream(EventStream):
    def __init__(self, api: FakeBriarApi) -> None:
        self.queue: asyncio.Queue[Event | BriarError] = asyncio.Queue()
        self._api = api

    async def next(self) -> Event:
        if self.queue.empty():
            self._api._idle.set()
        item = await self.queue.get()
        if isinstance(item, BriarError):
            raise item
        return item

    async def close(self) -> None:
        if self in self._api._streams:
            self._api._streams.remove(self)
