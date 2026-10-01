"""The messages that cross the extension API (docs/architecture.md §3)."""

from dataclasses import dataclass
from datetime import datetime

from chatko.domain import Account, Attachment, EndpointRef, MessageId, plain_text


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A message an extension read at one of its endpoints, for `HubContext.submit`.

    `transport_id` is the network's id of the message. It is unique within the endpoint and the
    same every time the network hands the extension the same message (after a reconnect, through
    another gateway), so that the hub can drop the copies. `author` is the account that posted it,
    with the names the network shows now; its kind is the extension's `type_name`. Non-text content
    goes into `attachments`, and its caption, if any, into `text`.
    """

    endpoint: EndpointRef
    transport_id: str
    author: Account
    text: str
    attachments: tuple[Attachment, ...] = ()

    def __post_init__(self) -> None:
        if not self.transport_id:
            raise ValueError(f"a message from {self.endpoint} needs a transport id")
        if not self.text.strip() and not self.attachments:
            raise ValueError(f"message {self.transport_id!r} from {self.endpoint} is empty")


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """A message the hub asks an extension to post at one of its endpoints.

    The hub has chosen the author label (design.md §8) and the text, which a routing target may
    have replaced. The extension renders them for its network: `formatted` is the standard form
    (`NatAda: [photo] caption`); a network with small packets shortens a long label and splits
    the text (design.md §6.3).

    `message_id`, the endpoint and `recipient` identify the delivery; `attempt` counts from 1, so
    an extension can check whether an earlier attempt got through before it posts again.
    `received_at` is when the hub received the original, for networks that show late messages
    with their time.
    """

    message_id: MessageId
    author_label: str
    text: str
    received_at: datetime
    attachments: tuple[Attachment, ...] = ()
    recipient: str | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        if not self.author_label.strip():
            raise ValueError(f"message {self.message_id} needs an author label")
        if self.received_at.utcoffset() is None:
            raise ValueError("a message's time must be timezone-aware")
        if self.attempt < 1:
            raise ValueError("attempts count from 1")

    @property
    def plain_text(self) -> str:
        """The text with a placeholder for each attachment in front: `[photo] caption`."""
        return plain_text(self.text, self.attachments)

    @property
    def formatted(self) -> str:
        """The label and the plain text, as a text-only network shows them: `NatAda: text`."""
        return f"{self.author_label}: {self.plain_text}"
