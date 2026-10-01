"""The core's `HubContext`: the hub as each extension instance sees it (architecture.md §3.3)."""

import logging
from datetime import datetime

from chatko.application.accounts import NewAccounts
from chatko.application.history import HubHistory
from chatko.application.outbox import OutboxWorker
from chatko.application.pipeline import InboundPipeline, Outcome
from chatko.application.ports import AdminNotices, Clock
from chatko.domain import Account, EndpointRef
from chatko.extension_api import HubContext, InboundMessage

_log = logging.getLogger("chatko.hub")


class ExtensionHub(HubContext):
    """One per extension instance. It accepts only the instance's own endpoints: a call about
    another instance's endpoint is logged and ignored. The authors of submitted messages and the
    accounts heard go to `accounts`, if given, for the new-account notice."""

    def __init__(
        self,
        instance: str,
        *,
        pipeline: InboundPipeline,
        outbox: OutboxWorker,
        history: HubHistory,
        notices: AdminNotices,
        clock: Clock,
        accounts: NewAccounts | None = None,
    ) -> None:
        self.instance = instance
        self._pipeline = pipeline
        self._outbox = outbox
        self._history = history
        self._notices = notices
        self._clock = clock
        self._accounts = accounts

    async def submit(self, message: InboundMessage) -> None:
        if not self._owns(message.endpoint, "a message"):
            return
        outcome = await self._pipeline.submit(message)
        if self._accounts is not None and outcome in (Outcome.ROUTED, Outcome.NOT_ROUTED):
            await self._accounts.saw(message.author, message.endpoint)

    async def heard(self, account: Account, endpoint: EndpointRef | None = None) -> None:
        if endpoint is None or self._owns(endpoint, f"that {account.key} was heard"):
            self._history.hear(account.key, self.now(), endpoint)
            if self._accounts is not None:
                await self._accounts.saw(account, endpoint)

    async def retry_now(self, endpoint: EndpointRef, recipient: str | None = None) -> None:
        if self._owns(endpoint, "a retry"):
            self._outbox.retry_now(endpoint, recipient)

    async def notify_admin(self, text: str, *, key: str | None = None) -> None:
        try:
            await self._notices.notify(text, key=f"{self.instance}:{text if key is None else key}")
        except Exception:
            _log.exception("an admin notice from %s was lost: %s", self.instance, text)

    def now(self) -> datetime:
        return self._clock.now()

    def _owns(self, endpoint: EndpointRef, what: str) -> bool:
        if endpoint.instance == self.instance:
            return True
        _log.error(
            "%s told the hub %s at %s, an endpoint of another instance",
            self.instance,
            what,
            endpoint,
        )
        return False
