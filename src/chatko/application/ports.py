"""What the application needs from outside (docs/architecture.md §5).

The infrastructure layer implements these; `chatko.application.testing` has in-memory fakes of them
for tests.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from chatko.domain import Delivery, EndpointRef, Message, MessageId


class Clock(Protocol):
    """The time, injected so that tests control it."""

    def now(self) -> datetime:
        """The current time, timezone-aware."""
        ...

    async def sleep_until(self, when: datetime) -> None:
        """Return at `when`, or at once if it has passed. Cancellable."""
        ...


class IdGenerator(Protocol):
    def new_message_id(self) -> MessageId:
        """An id no stored message has."""
        ...


class MessageRepository(Protocol):
    """The messages the hub received, with their outbox rows (design.md §9.1)."""

    async def contains(self, endpoint: EndpointRef, transport_id: str) -> bool:
        """Whether a message with this transport id from this endpoint is stored."""
        ...

    async def add(self, message: Message, deliveries: Sequence[Delivery]) -> bool:
        """Store a message and its deliveries together, in one transaction.

        Stores nothing and returns `False` if a message with the same endpoint and transport id is
        stored already. The deliveries join the outbox after all deliveries stored before them.
        """
        ...

    async def get(self, message_id: MessageId) -> Message:
        """The stored message with this id; `KeyError` if there is none."""
        ...


class OutboxRepository(Protocol):
    """The deliveries: one row per message, endpoint and recipient."""

    async def pending(self) -> list[Delivery]:
        """Every pending delivery, in the order they were stored."""
        ...

    async def save(self, delivery: Delivery) -> None:
        """Replace the stored delivery of the same message, endpoint and recipient; `KeyError` if
        there is none."""
        ...


class AdminNotices(Protocol):
    """Posts admin notices (design.md §2). The real one rate-limits them by key (roadmap S14)."""

    async def notify(self, text: str, *, key: str) -> None: ...


class RoutingScriptSource(Protocol):
    """Where the routing script's code comes from: `config/routing.py` (design.md §9)."""

    @property
    def origin(self) -> str:
        """The script's name in tracebacks and admin notices: its path."""
        ...

    async def read(self) -> str | None:
        """The script's code, or `None` when there is no script. Raises `OSError` or
        `UnicodeDecodeError` when there is one but it cannot be read."""
        ...
