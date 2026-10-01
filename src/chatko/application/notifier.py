"""Admin notices: messages from the hub to the admin (docs/design.md §2)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from chatko.application.installation import InstallationSource
from chatko.application.pipeline import DeliveryQueue
from chatko.application.ports import Clock, IdGenerator, MessageRepository
from chatko.domain import Account, AccountKey, Author, Delivery, EndpointRef, Message, Target

_log = logging.getLogger("chatko.notices")

NOTICE_SOURCE = EndpointRef("chatko:hub", "notices")
"""Where notices come from: no extension, so no delivery report goes anywhere."""

NOTICE_LABEL = "chatko"


@dataclass(slots=True)
class _Posted:
    at: datetime
    held: int = 0


class AdminNotifier:
    """Implements `AdminNotices`: posts a notice at the admin's endpoint through the outbox.

    A notice is stored as a message of its own with one delivery per recipient of the endpoint, so
    it is retried like any message. Notices with the same key are rate-limited together: after one
    is posted, the others of its key within `min_interval` are held back, and the next one that
    gets through says how many were. Keys that never had a notice held back are forgotten after
    the interval. Without an admin endpoint the notice is only logged. It never
    raises.
    """

    def __init__(
        self,
        *,
        target: Callable[[], EndpointRef | None],
        installation: InstallationSource,
        messages: MessageRepository,
        outbox: DeliveryQueue,
        clock: Clock,
        ids: IdGenerator,
        min_interval: timedelta = timedelta(minutes=10),
    ) -> None:
        self._target = target
        self._installation = installation
        self._messages = messages
        self._outbox = outbox
        self._clock = clock
        self._ids = ids
        self._min_interval = min_interval
        self._posted: dict[str, _Posted] = {}

    async def notify(self, text: str, *, key: str) -> None:
        try:
            await self._notify(text, key)
        except Exception:
            _log.exception("an admin notice was lost: %s", text)

    async def _notify(self, text: str, key: str) -> None:
        endpoint = self._target()
        if endpoint is None:
            _log.warning("admin notice (no endpoint for notices is configured): %s", text)
            return
        now = self._clock.now()
        earlier = self._posted.get(key)
        if earlier is not None and now - earlier.at < self._min_interval:
            earlier.held += 1
            _log.info("admin notice held back (%s): %s", key, text)
            return
        held = 0 if earlier is None else earlier.held
        self._posted = {
            k: p for k, p in self._posted.items() if k != key and (p.held or self._fresh(p, now))
        }
        self._posted[key] = _Posted(now)
        body = text if not held else f"{text}\n({held} similar notices were held back before)"
        message_id = self._ids.new_message_id()
        message = Message(
            message_id,
            NOTICE_SOURCE,
            f"notice:{message_id}",
            Author(Account(AccountKey("chatko", "hub"), NOTICE_LABEL)),
            body,
            now,
        )
        recipients = self._installation().recipients(endpoint) or (None,)
        deliveries = [
            Delivery.of(message, Target(endpoint), NOTICE_LABEL, now, recipient)
            for recipient in recipients
        ]
        if await self._messages.add(message, deliveries):
            self._outbox.enqueue(deliveries)
            _log.info("admin notice posted at %s (%s): %s", endpoint, key, text)

    def _fresh(self, posted: _Posted, now: datetime) -> bool:
        return now - posted.at < self._min_interval
