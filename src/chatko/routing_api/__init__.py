"""The only package a routing script may import (docs/architecture.md §4).

It re-exports the domain types a script needs, so a script imports nothing else of chatko.
"""

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
from chatko.routing_api.script import RoutingScript, ScriptError
from chatko.routing_api.version import API_VERSION, is_supported

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
    "RoutingScript",
    "ScriptError",
    "Target",
    "default_label",
    "is_supported",
    "mirror",
    "to_endpoint",
]
