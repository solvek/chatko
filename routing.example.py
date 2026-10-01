"""Example routing script. Copy to config/routing.py and edit.

The hub calls `route` for every incoming message and delivers it to the targets it returns. The core
always drops echoes and duplicates, whatever this function returns (docs/design.md §9.3). The API
is described in docs/architecture.md §4.
"""

from datetime import timedelta

from chatko.routing_api import (
    AccountKey,
    EndpointRef,
    RoutedMessage,
    RoutingContext,
    Target,
    default_label,
    mirror,
    to_endpoint,
)

api_version = (1, 0)  # optional: the version of chatko.routing_api this script is written for

LATELY = timedelta(hours=1)


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

    # Everything else: all other sites of the message's group.
    return [skip_radios_on_the_channel(target, ctx) for target in mirror(msg, ctx)]


def skip_radios_on_the_channel(target: Target, ctx: RoutingContext) -> Target:
    """Radios heard on the family channel lately get its copy there, not a direct message too."""
    if target.endpoint != ctx.endpoint("family.radio"):
        return target
    channel = ctx.endpoint("family.channel")
    quiet = [
        node
        for node in ctx.recipients(target.endpoint)
        if not heard_lately(AccountKey("meshtastic", node), channel, ctx)
    ]
    return to_endpoint(target.endpoint, recipients=quiet)


def heard_lately(account: AccountKey, endpoint: EndpointRef, ctx: RoutingContext) -> bool:
    heard = ctx.last_heard(account, endpoint)
    return heard is not None and ctx.now - heard < LATELY


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
