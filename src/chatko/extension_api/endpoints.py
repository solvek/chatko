"""The endpoint capability of an extension (docs/architecture.md §3)."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from chatko.domain import EndpointRef
from chatko.extension_api.delivery import DeliveryReport, DeliveryResult
from chatko.extension_api.messages import OutboundMessage


class EndpointProvider[E: BaseModel](ABC):
    """An extension that reads and posts messages at endpoints: chats, groups, channels.

    The admin names every endpoint (D34) and the core passes it as an `EndpointRef`; what the
    endpoint is in the network (a chat id, a channel, a list of nodes) is in its config, which only
    the extension reads. The extension does not know whether an endpoint is a site of a group or
    a source.
    """

    endpoint_config_model: ClassVar[type[BaseModel]]
    """Validates one endpoint's config, without `ext`. It should reject unknown keys."""

    @abstractmethod
    def set_endpoints(self, endpoints: Mapping[EndpointRef, E]) -> None:
        """Take the instance's complete set of endpoints, in the order of the config.

        Called before `start`, and again whenever the set changes; apply the difference. Read
        only the endpoints in the latest set and ignore every other place in the network. Do no
        I/O here: start what a new endpoint needs (e.g. a catch-up) in the background. Raise
        `ValueError`, naming the endpoint, for a set that cannot work with the instance's config
        (e.g. a channel the node does not have); the core checks every new set on a fresh,
        unstarted instance first, so a running one only gets valid sets.
        """

    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]:
        """The recipients that the endpoint reaches separately (the nodes of a Meshtastic `dm`
        endpoint), or `()` when it is one place (a chat, a channel); the default.

        The hub then delivers, retries and reports each message to each recipient on its own,
        with `OutboundMessage.recipient` set.
        """
        del endpoint
        return ()

    @abstractmethod
    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        """Post a message at one of the endpoints (to one recipient, if it has recipients).

        For one endpoint and recipient, the hub makes one call at a time, oldest message first,
        and a `Retry` holds back the newer messages; different endpoints and recipients are
        served concurrently. `message.recipient` is one of `recipients(endpoint)`, or `None` for
        an endpoint without recipients. Answer `Retry` when the network is down or did not
        confirm, and `Failed` when trying again cannot help, including for an endpoint or a
        recipient no longer in the config. An exception counts as `Retry`. A delivery is at
        least once: after a crash an attempt may be repeated, and `message.attempt` tells a
        repeated attempt from the first.
        """

    async def delivery_report(self, report: DeliveryReport) -> None:
        """Learn how a message from one of this extension's endpoints fared at one target, once
        that delivery ended. The default ignores it; a network may show it (a reaction).
        """
        del report
