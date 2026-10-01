"""In-memory implementations of the ports of `chatko.application.ports`."""

import asyncio
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from chatko.domain import Delivery, DeliveryState, EndpointRef, Message, MessageId

DEFAULT_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

type _DeliveryKey = tuple[MessageId, EndpointRef, str | None]


class FakeClock:
    """A clock that stands still until a test moves it with `advance` or `set`."""

    def __init__(self, time: datetime = DEFAULT_TIME) -> None:
        if time.utcoffset() is None:
            raise ValueError("the clock's time must be timezone-aware")
        self._now = time
        self._sleepers: list[tuple[datetime, asyncio.Future[None]]] = []

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        if when <= self._now:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        self._sleepers.append((when, future))
        await future

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward and wake the sleepers that are due."""
        if delta < timedelta(0):
            raise ValueError("the clock does not go back")
        self.set(self._now + delta)

    def set(self, time: datetime) -> None:
        self._now = time
        waiting = []
        for when, future in self._sleepers:
            if future.done():
                continue
            if when <= time:
                future.set_result(None)
            else:
                waiting.append((when, future))
        self._sleepers = waiting

    @property
    def sleepers(self) -> int:
        """How many sleepers are waiting for a later time."""
        return sum(1 for _, future in self._sleepers if not future.done())


class SequentialIds:
    """Message ids `m1`, `m2`, …"""

    def __init__(self) -> None:
        self._numbers = itertools.count(1)

    def new_message_id(self) -> MessageId:
        return MessageId(f"m{next(self._numbers)}")


class InMemoryStore:
    """Implements `MessageRepository` and `OutboxRepository` over shared dictionaries, as one
    database would."""

    def __init__(self) -> None:
        self.messages: dict[MessageId, Message] = {}
        self._transport_ids: set[tuple[EndpointRef, str]] = set()
        self._deliveries: dict[_DeliveryKey, Delivery] = {}

    async def contains(self, endpoint: EndpointRef, transport_id: str) -> bool:
        return (endpoint, transport_id) in self._transport_ids

    async def add(self, message: Message, deliveries: Sequence[Delivery]) -> bool:
        if (message.endpoint, message.transport_id) in self._transport_ids:
            return False
        if message.id in self.messages:
            raise ValueError(f"message {message.id} is stored already")
        keys = [_key(delivery) for delivery in deliveries]
        if len(set(keys)) != len(keys) or any(key in self._deliveries for key in keys):
            raise ValueError(f"the deliveries of {message.id} repeat a destination")
        if any(delivery.message_id != message.id for delivery in deliveries):
            raise ValueError(f"a delivery is not one of {message.id}")
        self.messages[message.id] = message
        self._transport_ids.add((message.endpoint, message.transport_id))
        self._deliveries.update(zip(keys, deliveries, strict=True))
        return True

    async def get(self, message_id: MessageId) -> Message:
        try:
            return self.messages[message_id]
        except KeyError:
            raise KeyError(f"no message {message_id}") from None

    async def pending(self) -> list[Delivery]:
        return [d for d in self._deliveries.values() if d.state is DeliveryState.PENDING]

    async def save(self, delivery: Delivery) -> None:
        key = _key(delivery)
        if key not in self._deliveries:
            raise KeyError(f"no delivery of {delivery.message_id} to {delivery.destination}")
        self._deliveries[key] = delivery

    @property
    def deliveries(self) -> list[Delivery]:
        """Every delivery, in the order they were stored."""
        return list(self._deliveries.values())


def _key(delivery: Delivery) -> _DeliveryKey:
    return delivery.message_id, delivery.endpoint, delivery.recipient


@dataclass(frozen=True, slots=True)
class RecordedNotice:
    text: str
    key: str


class RecordingNotices:
    """Records admin notices instead of posting them."""

    def __init__(self) -> None:
        self.notices: list[RecordedNotice] = []

    async def notify(self, text: str, *, key: str) -> None:
        self.notices.append(RecordedNotice(text, key))


class InMemoryScriptSource:
    """Implements `RoutingScriptSource` with code a test sets: `code = None` is no script, and
    `error` is raised by the next reads instead."""

    def __init__(self, code: str | None = None, origin: str = "routing.py") -> None:
        self.code = code
        self.error: OSError | UnicodeDecodeError | None = None
        self._origin = origin

    @property
    def origin(self) -> str:
        return self._origin

    async def read(self) -> str | None:
        if self.error is not None:
            raise self.error
        return self.code
