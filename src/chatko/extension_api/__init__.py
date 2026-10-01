"""The only package extensions may import (docs/architecture.md §3).

It re-exports the domain types an extension needs, so an extension imports nothing else of chatko.
"""

from typing import Final

from chatko.domain import Account, AccountKey, Attachment, AttachmentKind, EndpointRef, MessageId
from chatko.extension_api.delivery import Delivered, DeliveryReport, DeliveryResult, Failed, Retry
from chatko.extension_api.endpoints import EndpointProvider
from chatko.extension_api.extension import Extension
from chatko.extension_api.hub import HubContext
from chatko.extension_api.messages import InboundMessage, OutboundMessage

API_VERSION: Final = (1, 0)
"""The version of this API: (major, minor). A minor version only adds; a major one breaks."""


def is_supported(version: tuple[int, int]) -> bool:
    """Whether an extension written for API `version` runs on this one: the same major version,
    and a minor version no newer than this one's.
    """
    major, minor = version
    return major == API_VERSION[0] and minor <= API_VERSION[1]


__all__ = [
    "API_VERSION",
    "Account",
    "AccountKey",
    "Attachment",
    "AttachmentKind",
    "Delivered",
    "DeliveryReport",
    "DeliveryResult",
    "EndpointProvider",
    "EndpointRef",
    "Extension",
    "Failed",
    "HubContext",
    "InboundMessage",
    "MessageId",
    "OutboundMessage",
    "Retry",
    "is_supported",
]
