"""The only package a routing script may import (docs/architecture.md §4).

It re-exports the domain types a script needs, so a script imports nothing else of chatko.
"""

from typing import Final

from chatko.domain import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Author,
    EndpointRef,
    Fingerprint,
    Group,
    MessageId,
    Person,
    Target,
)
from chatko.routing_api.context import RoutingContext, RoutingHistory
from chatko.routing_api.helpers import (
    LabelFunction,
    RouteFunction,
    default_label,
    mirror,
    to_endpoint,
)
from chatko.routing_api.messages import RoutedMessage

API_VERSION: Final = (1, 0)
"""The version of this API: (major, minor). A minor version only adds; a major one breaks."""


def is_supported(version: tuple[int, int]) -> bool:
    """Whether a script written for API `version` runs on this one: the same major version, and
    a minor version no newer than this one's.
    """
    major, minor = version
    return major == API_VERSION[0] and minor <= API_VERSION[1]


__all__ = [
    "API_VERSION",
    "Account",
    "AccountKey",
    "Attachment",
    "AttachmentKind",
    "Author",
    "EndpointRef",
    "Fingerprint",
    "Group",
    "LabelFunction",
    "MessageId",
    "Person",
    "RouteFunction",
    "RoutedMessage",
    "RoutingContext",
    "RoutingHistory",
    "Target",
    "default_label",
    "is_supported",
    "mirror",
    "to_endpoint",
]
