"""S28: default routing, but a radio heard lately on the family channel gets no direct message too."""

from datetime import timedelta

from chatko.routing_api import AccountKey, RoutedMessage, RoutingContext, Target, mirror, to_endpoint

LATELY = timedelta(hours=1)


def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    return [skip_radios_on_the_channel(t, ctx) for t in mirror(msg, ctx)]


def skip_radios_on_the_channel(target: Target, ctx: RoutingContext) -> Target:
    if target.endpoint != ctx.endpoint("family.radio"):
        return target
    channel = ctx.endpoint("family.channel")
    quiet = []
    for node in ctx.recipients(target.endpoint):
        if not target.includes(node):
            continue
        heard = ctx.last_heard(AccountKey("meshtastic", node), channel)
        if heard is None or ctx.now - heard >= LATELY:
            quiet.append(node)
    return to_endpoint(target.endpoint, recipients=quiet)
