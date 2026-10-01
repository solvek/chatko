"""The inbound pipeline: from a submitted message to its outbox rows (docs/design.md §9.1)."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from chatko.application.history import HubHistory
from chatko.application.installation import Installation, InstallationSource
from chatko.application.invariants import RoutingInvariants
from chatko.application.ports import Clock, IdGenerator, MessageRepository
from chatko.application.routing import Router
from chatko.domain import Delivery, Message, Target
from chatko.extension_api import InboundMessage
from chatko.routing_api import RoutedMessage, RoutingContext

_log = logging.getLogger("chatko.pipeline")


class Outcome(StrEnum):
    """What became of a submitted message."""

    ROUTED = "routed"
    """Stored, with one outbox row per destination (possibly none)."""
    NOT_ROUTED = "not routed"
    """Stored but not routed: another path brought the same message in lately (§9.3)."""
    COPY = "copy"
    """Dropped: a message with the same transport id from the same endpoint is stored."""
    UNKNOWN_ENDPOINT = "unknown endpoint"
    """Dropped: the endpoint is not in the config."""


class DeliveryQueue(Protocol):
    def enqueue(self, deliveries: Sequence[Delivery]) -> None:
        """Take deliveries that were just stored."""
        ...


class InboundPipeline:
    """Stores each submitted message once, routes it, and writes its outbox rows.

    For every message: drop it if its endpoint is unknown or it is a copy (the same transport id
    from the same endpoint); find its author's person; route it, unless the endpoint
    de-duplicates by fingerprint and the same message arrived lately; apply the invariants; label
    it for each target; store the message with one delivery per target and recipient in one
    transaction; hand the deliveries to the outbox worker.

    Messages are handled one at a time, in the order they were submitted, so the outbox keeps
    that order and `RoutingContext.seen` counts exactly the messages before. Cancelling `submit`
    does not cancel the handling: the message is stored or not, as a whole.
    """

    def __init__(
        self,
        *,
        installation: InstallationSource,
        messages: MessageRepository,
        outbox: DeliveryQueue,
        router: Router,
        history: HubHistory,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._installation = installation
        self._messages = messages
        self._outbox = outbox
        self._router = router
        self._history = history
        self._clock = clock
        self._ids = ids
        self._lock = asyncio.Lock()

    async def submit(self, inbound: InboundMessage) -> Outcome:
        task = asyncio.create_task(self._submit(inbound))
        task.add_done_callback(_log_unexpected_error)
        return await asyncio.shield(task)

    async def _submit(self, inbound: InboundMessage) -> Outcome:
        async with self._lock:
            installation = self._installation()
            topology = installation.topology
            endpoint = inbound.endpoint
            if not topology.has_endpoint(endpoint):
                _log.warning(
                    "dropped message %r from %s: no such endpoint", inbound.transport_id, endpoint
                )
                return Outcome.UNKNOWN_ENDPOINT
            if await self._messages.contains(endpoint, inbound.transport_id):
                _log.debug("dropped a copy of message %r from %s", inbound.transport_id, endpoint)
                return Outcome.COPY

            now = self._clock.now()
            message = Message(
                self._ids.new_message_id(),
                endpoint,
                inbound.transport_id,
                topology.author_of(inbound.author),
                inbound.text,
                now,
                inbound.attachments,
            )
            routed = RoutedMessage(message, topology.group_of(endpoint))
            window = installation.fingerprint_dedup.get(endpoint)
            if window is not None and self._history.seen_since(routed.fingerprint, now - window):
                _log.info("message %s from %s came by another path already", message.id, endpoint)
                outcome, deliveries = Outcome.NOT_ROUTED, []
            else:
                outcome, deliveries = Outcome.ROUTED, self._plan(routed, message, installation, now)

            if not await self._messages.add(message, deliveries):
                return Outcome.COPY
            self._history.see(routed.fingerprint, now)
            self._history.hear(inbound.author.key, now, endpoint)
            self._outbox.enqueue(deliveries)
            _log.info(
                "message %s from %s: %s to %s",
                message.id,
                endpoint,
                outcome,
                ", ".join(delivery.destination for delivery in deliveries) or "nowhere",
            )
            return outcome

    def _plan(
        self, routed: RoutedMessage, message: Message, installation: Installation, now: datetime
    ) -> list[Delivery]:
        recipients = installation.all_recipients()
        ctx = RoutingContext(
            installation.topology,
            now=now,
            extension_types=installation.extension_types,
            recipients=recipients,
            history=self._history,
        )
        targets = self._router.route(routed, ctx)
        destinations = RoutingInvariants(installation.topology, recipients).apply(
            routed.endpoint, targets
        )
        labels: dict[Target, str] = {}
        deliveries = []
        for destination in destinations:
            target = destination.target
            if target not in labels:
                labels[target] = (
                    self._router.label(routed, target, ctx)
                    if target.label is None
                    else target.label
                )
            deliveries.append(
                Delivery.of(message, target, labels[target], now, destination.recipient)
            )
        return deliveries


def _log_unexpected_error(task: asyncio.Task[Outcome]) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        _log.error("handling a submitted message failed", exc_info=error)
