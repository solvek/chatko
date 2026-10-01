"""Example routing script. Copy to config/routing.py and edit.

The hub calls `route` for every incoming message and delivers it to the targets it returns. The core
always drops echoes and duplicates, whatever this function returns (docs/design.md §9.3).
This is a sketch: the real API is settled in phase 1 (docs/architecture.md §4).
"""

from chatko.routing_api import (
    Author,
    RoutedMessage,
    RoutingContext,
    Target,
    default_label,
    mirror,
    to_endpoint,
)


def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    # A feed: the public LongFast chat goes to the owner's private chat with the bot.
    if msg.endpoint == ctx.source("longfast"):
        return [to_endpoint(ctx.source("owner"))]

    # A tagged message from the family group is also posted to the street group.
    if msg.group == ctx.group("family") and msg.text.startswith("#street "):
        text = msg.text.removeprefix("#street ")
        return mirror(msg, ctx) + [
            to_endpoint(site, text=text) for site in ctx.group("street").sites
        ]

    # Everything else: all other sites of the message's group.
    return mirror(msg, ctx)


# Optional. Without this function the core uses default_label: the person's label from the config
# (`NatAda`), or a short Latin form of the name the network gives.
def label(author: Author, target: Target, ctx: RoutingContext) -> str:
    # Mark accounts that are not in `people`, so a look-alike name cannot pass for a known person.
    if author.person is None:
        return "~" + default_label(author, ctx)
    return default_label(author, ctx)
