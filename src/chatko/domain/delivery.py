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

    `recipient` is set when the endpoint has recipients that are reached separately (the nodes of
    a Meshtastic `dm` endpoint): such a message gets one delivery per recipient (design.md §9.1).

    A pending delivery is attempted once it is due. `begin_attempt` counts the attempt before the
    network is called, so that after a crash in the middle of one the next attempt is known to be a
    repeat. Each outcome gives a new `Delivery`: delivered and failed are final, a retry stays
    pending with a later due time.
    """

    message_id: MessageId
    endpoint: EndpointRef
    author_label: str
    text: str
    due_at: datetime
    recipient: str | None = None
    state: DeliveryState = DeliveryState.PENDING
    attempts: int = 0
    truncated: bool = False
    last_error: str | None = None

    def __post_init__(self) -> None:
        if not self.author_label.strip():
            raise DomainError(f"a delivery to {self.endpoint} needs an author label")
        if self.recipient is not None and not self.recipient.strip():
            raise DomainError(f"a delivery to {self.endpoint} has an empty recipient")
        if self.due_at.utcoffset() is None:
            raise DomainError("a delivery's due time must be timezone-aware")
        if self.attempts < 0:
            raise DomainError("attempts cannot be negative")

    @classmethod
    def of(
        cls,
        message: Message,
        target: Target,
        author_label: str,
        now: datetime,
        recipient: str | None = None,
    ) -> Self:
        """A new pending delivery, due now. The target's text, if given, replaces the message's."""
        text = message.text if target.text is None else target.text
        return cls(message.id, target.endpoint, author_label, text, due_at=now, recipient=recipient)

    @property
    def destination(self) -> str:
        """Where it goes, for logs: `kyiv/family.radio`, or `kyiv/family.radio:!a1b2c3d4` with a
        recipient."""
        return str(self.endpoint) if self.recipient is None else f"{self.endpoint}:{self.recipient}"

    def is_due(self, now: datetime) -> bool:
        return self.state is DeliveryState.PENDING and self.due_at <= now

    def begin_attempt(self) -> Self:
        """The same delivery with one more attempt counted: the hub is about to try it."""
        self._check_pending()
        return replace(self, attempts=self.attempts + 1)

    def delivered(self, *, truncated: bool = False) -> Self:
        """The endpoint has it; `truncated` when the network could take only part of the text."""
        return self._ended(DeliveryState.DELIVERED, truncated=truncated)

    def retry(self, at: datetime, error: str) -> Self:
        """Not delivered this time; try again at `at`."""
        if at.utcoffset() is None:
            raise DomainError("a retry time must be timezone-aware")
        return self._ended(DeliveryState.PENDING, due_at=at, last_error=error)

    def failed(self, error: str) -> Self:
        """Not delivered, and retrying cannot help."""
        return self._ended(DeliveryState.FAILED, last_error=error)

    def _check_pending(self) -> None:
        if self.state is not DeliveryState.PENDING:
            raise DomainError(
                f"the delivery of {self.message_id} to {self.destination} is already {self.state}"
            )

    def _ended(
        self,
        state: DeliveryState,
        *,
        due_at: datetime | None = None,
        truncated: bool = False,
        last_error: str | None = None,
    ) -> Self:
        self._check_pending()
        return replace(
            self,
            state=state,
            due_at=self.due_at if due_at is None else due_at,
            truncated=truncated,
            last_error=last_error,
        )
