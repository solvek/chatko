"""The rules the core enforces on whatever the router returns (docs/design.md §9.3)."""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from chatko.domain import EndpointRef, Target, Topology

_log = logging.getLogger("chatko.routing")


@dataclass(frozen=True, slots=True)
class Destination:
    """One delivery to make: a target, and the recipient of its endpoint if it has recipients."""

    target: Target
    recipient: str | None = None

    @property
    def endpoint(self) -> EndpointRef:
        return self.target.endpoint


class RoutingInvariants:
    """Turns the targets of a message into destinations that keep the invariants:

    - nothing goes back to where the message came from: the endpoint, or for an endpoint with
      recipients the recipient that posted it (`from_recipient`; the whole endpoint if it is not
      known);
    - nothing goes to an endpoint the topology does not have (it is logged);
    - each endpoint, and each recipient of an endpoint that has recipients, gets the message at
      most once: the first target that includes it wins;
    - a target reaches only the endpoint's own recipients, all of them unless it narrows them.
    """

    def __init__(
        self, topology: Topology, recipients: Mapping[EndpointRef, tuple[str, ...]]
    ) -> None:
        self._topology = topology
        self._recipients = recipients

    def apply(
        self,
        source: EndpointRef,
        targets: Iterable[Target],
        from_recipient: str | None = None,
    ) -> list[Destination]:
        destinations: list[Destination] = []
        # The source endpoint is open to the other recipients only if it is known who posted.
        source_open = from_recipient is not None and bool(self._recipients.get(source))
        taken: set[tuple[EndpointRef, str | None]] = {(source, from_recipient)}
        for target in targets:
            endpoint = target.endpoint
            if endpoint == source and not source_open:
                _log.debug("dropped a target back at the source %s", source)
                continue
            if not self._topology.has_endpoint(endpoint):
                _log.warning("dropped a target at %s, which is not in the config", endpoint)
                continue
            for recipient in self._recipients_of(target):
                if (endpoint, recipient) not in taken:
                    taken.add((endpoint, recipient))
                    destinations.append(Destination(target, recipient))
        return destinations

    def _recipients_of(self, target: Target) -> list[str | None]:
        recipients = self._recipients.get(target.endpoint, ())
        if not recipients:
            return [None]
        return [recipient for recipient in recipients if target.includes(recipient)]
