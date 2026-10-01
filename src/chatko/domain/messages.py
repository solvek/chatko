"""Messages and routing targets (docs/design.md §9)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

from chatko.domain.accounts import Author
from chatko.domain.endpoints import EndpointRef
from chatko.domain.errors import DomainError
from chatko.domain.fingerprint import Fingerprint, fingerprint
from chatko.domain.labels import default_label

MessageId = NewType("MessageId", str)
"""The hub's own id of a stored message, made by the id generator port."""


class AttachmentKind(StrEnum):
    """Non-text content in network-neutral terms. v1 relays it as a placeholder (design.md §6.3)."""

    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    FILE = "file"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACT = "contact"
    POLL = "poll"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Attachment:
    kind: AttachmentKind

    @property
    def placeholder(self) -> str:
        """How a text-only network shows the attachment: `[photo]`."""
        return f"[{self.kind}]"


def plain_text(text: str, attachments: Iterable[Attachment]) -> str:
    """A text as a text-only network shows it, with its attachments as placeholders in front."""
    return " ".join([a.placeholder for a in attachments] + [text]).strip()


@dataclass(frozen=True, slots=True)
class Message:
    """An incoming message as the hub stores and routes it.

    `transport_id` is the id the network gave it, unique within its endpoint; the core uses it to
    drop copies of one message that arrive more than once (design.md §9.1).
    """

    id: MessageId
    endpoint: EndpointRef
    transport_id: str
    author: Author
    text: str
    received_at: datetime
    attachments: tuple[Attachment, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise DomainError("a message needs an id")
        if not self.transport_id:
            raise DomainError(f"a message from {self.endpoint} needs a transport id")
        if self.received_at.utcoffset() is None:
            raise DomainError("a message's time must be timezone-aware")
        if not self.text.strip() and not self.attachments:
            raise DomainError(f"message {self.transport_id!r} from {self.endpoint} is empty")

    @property
    def plain_text(self) -> str:
        """The text with a placeholder for each attachment in front: `[photo] caption`."""
        return plain_text(self.text, self.attachments)

    @property
    def fingerprint(self) -> Fingerprint:
        """The fingerprint of the default author label and the plain text (design.md §9.5)."""
        return fingerprint(default_label(self.author), self.plain_text)


@dataclass(frozen=True, slots=True)
class Target:
    """Where a routed message goes, with optional overrides of its text and author label.

    `recipients` narrows the delivery to some of the endpoint's recipients (the nodes of a
    Meshtastic `dm` endpoint, design.md §9.2); `None` means all of them, and an empty set none. It
    has no effect on an endpoint without recipients.
    """

    endpoint: EndpointRef
    text: str | None = None
    label: str | None = None
    recipients: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.label is not None and not self.label.strip():
            raise DomainError(f"the label for {self.endpoint} is empty")

    def includes(self, recipient: str) -> bool:
        """Whether the message goes to `recipient`, one of the endpoint's recipients."""
        return self.recipients is None or recipient in self.recipients
