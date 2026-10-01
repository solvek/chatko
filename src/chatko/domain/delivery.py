"""Deliveries: one outbox row per target of a message (docs/design.md §9.1, step 5)."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from chatko.domain.endpoints import EndpointRef
from chatko.domain.errors import DomainError
from chatko.domain.messages import Message, MessageId, Target


class DeliveryState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Delivery:
    """A message on its way to one endpoint, with the label and text it goes out with.

    A pending delivery is attempted once it is due. Each outcome counts as an attempt and gives a
    new `Delivery`: delivered and failed are final, a retry stays pending with a later due time.
    """

    message_id: MessageId
    endpoint: EndpointRef
    author_label: str
    text: str
    due_at: datetime
    state: DeliveryState = DeliveryState.PENDING
    attempts: int = 0
    truncated: bool = False
    last_error: str | None = None

    def __post_init__(self) -> None:
        if not self.author_label.strip():
            raise DomainError(f"a delivery to {self.endpoint} needs an author label")
        if self.due_at.utcoffset() is None:
            raise DomainError("a delivery's due time must be timezone-aware")
        if self.attempts < 0:
            raise DomainError("attempts cannot be negative")

    @classmethod
    def of(cls, message: Message, target: Target, author_label: str, now: datetime) -> Self:
        """A new pending delivery, due now. The target's text, if given, replaces the message's."""
        text = message.text if target.text is None else target.text
        return cls(message.id, target.endpoint, author_label, text, due_at=now)

    def is_due(self, now: datetime) -> bool:
        return self.state is DeliveryState.PENDING and self.due_at <= now

    def delivered(self, *, truncated: bool = False) -> Self:
        """The endpoint has it; `truncated` when the network could take only part of the text."""
        return self._attempted(DeliveryState.DELIVERED, truncated=truncated)

    def retry(self, at: datetime, error: str) -> Self:
        """Not delivered this time; try again at `at`."""
        if at.utcoffset() is None:
            raise DomainError("a retry time must be timezone-aware")
        return self._attempted(DeliveryState.PENDING, due_at=at, last_error=error)

    def failed(self, error: str) -> Self:
        """Not delivered, and retrying cannot help."""
        return self._attempted(DeliveryState.FAILED, last_error=error)

    def _attempted(
        self,
        state: DeliveryState,
        *,
        due_at: datetime | None = None,
        truncated: bool = False,
        last_error: str | None = None,
    ) -> Self:
        if self.state is not DeliveryState.PENDING:
            raise DomainError(
                f"the delivery of {self.message_id} to {self.endpoint} is already {self.state}"
            )
        return replace(
            self,
            state=state,
            attempts=self.attempts + 1,
            due_at=self.due_at if due_at is None else due_at,
            truncated=truncated,
            last_error=last_error,
        )
