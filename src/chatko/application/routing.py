"""The router the pipeline calls: the routing script, or the defaults (docs/design.md §9.4)."""

from collections.abc import Sequence
from typing import Protocol

from chatko.domain import Target
from chatko.routing_api import RoutedMessage, RoutingContext, default_label, mirror


class Router(Protocol):
    """Decides where a message goes and how its author is signed. Never raises: the routing
    engine (roadmap S12) handles a script's errors by falling back to the defaults."""

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]: ...

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str: ...


class DefaultRouter:
    """The defaults: `mirror` and `default_label`, as without a routing script."""

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]:
        return mirror(msg, ctx)

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        del target
        return default_label(msg.author, ctx)
