"""Entities and pure rules. No I/O, no asyncio, no databases (docs/architecture.md §1)."""

from chatko.domain.accounts import Account, AccountKey, Author, Person
from chatko.domain.delivery import Delivery, DeliveryState
from chatko.domain.endpoints import EndpointRef, Group
from chatko.domain.errors import DomainError
from chatko.domain.fingerprint import Fingerprint, fingerprint, normalize_label, normalize_text
from chatko.domain.labels import MAX_LABEL_LENGTH, default_label, generate_label
from chatko.domain.messages import Attachment, AttachmentKind, Message, MessageId, Target
from chatko.domain.topology import Topology
from chatko.domain.transliteration import transliterate

__all__ = [
    "MAX_LABEL_LENGTH",
    "Account",
    "AccountKey",
    "Attachment",
    "AttachmentKind",
    "Author",
    "Delivery",
    "DeliveryState",
    "DomainError",
    "EndpointRef",
    "Fingerprint",
    "Group",
    "Message",
    "MessageId",
    "Person",
    "Target",
    "Topology",
    "default_label",
    "fingerprint",
    "generate_label",
    "normalize_label",
    "normalize_text",
    "transliterate",
]
