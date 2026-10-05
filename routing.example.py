"""Example routing script. Copy to config/routing.py and edit.

The hub calls `route` for every incoming message and delivers it to the targets it returns. The core
always drops echoes and duplicates, whatever this function returns (docs/design.md §9.3). The API
is described in docs/architecture.md §4.
"""

from chatko.routing_api import (
    RoutedMessage,
    RoutingContext,
    Target,
    default_label,
    mirror,
    to_endpoint,
)

api_version = (1, 0)  # optional: the version of chatko.routing_api this script is written for


def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    # A feed: the mesh's LongFast chat goes to the owner's private chat with the bot.
    if msg.endpoint == ctx.source("longfast"):
        return [to_endpoint(ctx.source("owner"))]

    # A tagged message from the family group is also posted to the street group.
    if msg.group == ctx.group("family") and msg.text.startswith("#street "):
        text = msg.text.removeprefix("#street ")
        return mirror(msg, ctx) + [
            to_endpoint(site, text=text) for site in ctx.group("street").sites
        ]

    # Everything else: all other sites of the message's group. (A group with both a `channel` and
    # a `dm` site gives a radio on both two copies; design.md §6.2 says how to avoid that.)
    return mirror(msg, ctx)


# Optional. Without this function the core uses default_label: the person's label from the config
# (`NatAda`), or a short Latin form of the name the network gives.
def label(msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
    account = msg.author.account
    # The mesh's chat is signed with the node's short and long name: `[BC1] Base Camp`.
    if msg.endpoint == ctx.source("longfast") and account.short_name:
        return f"[{account.short_name}] {account.display_name}"
    # Mark accounts that are not in `people`, so a look-alike name cannot pass for a known person.
    if msg.author.person is None:
        return "~" + default_label(msg.author, ctx)
    return default_label(msg.author, ctx)
