"""Building blocks for routing scripts (docs/design.md §9.4, §9.5)."""

from collections.abc import Callable, Iterable

from chatko.domain import Author, EndpointRef, Target
from chatko.domain import default_label as _domain_default_label
from chatko.routing_api.context import RoutingContext
from chatko.routing_api.messages import RoutedMessage

type RouteFunction = Callable[[RoutedMessage, RoutingContext], Iterable[Target]]
"""`route(msg, ctx)`: the targets of a message. Every script defines it."""

type LabelFunction = Callable[[RoutedMessage, Target, RoutingContext], str]
"""`label(msg, target, ctx)`: the author label for one target. Optional in a script."""


def to_endpoint(
    endpoint: EndpointRef,
    *,
    text: str | None = None,
    label: str | None = None,
    recipients: Iterable[str] | None = None,
) -> Target:
    """A target: the endpoint, optionally with another text or author label for it, or only
    some of its recipients (an empty list: none)."""
    return Target(endpoint, text, label, None if recipients is None else frozenset(recipients))


def mirror(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    """The default routing: all other sites of the message's group; nothing for a source."""
    group = ctx.group_of(msg.endpoint)
    if group is None:
        return []
    return [to_endpoint(site) for site in group.other_sites(msg.endpoint)]


def default_label(author: Author, ctx: RoutingContext) -> str:
    """The label the core uses without a `label` hook (design.md §8): a peer's label, the
    person's from the config, or one generated from the name the network gives."""
    del ctx  # Part of the signature, so that the default may depend on the installation later.
    return _domain_default_label(author)
