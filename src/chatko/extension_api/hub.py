"""What the hub offers an extension (docs/architecture.md §3)."""

from abc import ABC, abstractmethod
from datetime import datetime

from chatko.domain import Account, EndpointRef
from chatko.extension_api.messages import InboundMessage


class HubContext(ABC):
    """The hub as one extension instance sees it: the only object of the core it gets.

    The core implements it, and `chatko.extension_api.testing.FakeHub` fakes it. Call it from the
    event loop the extension runs in (a thread-based client library hands its callbacks over with
    `loop.call_soon_threadsafe`), and only between `Extension.start` and the end of
    `Extension.stop`.
    """

    @abstractmethod
    async def submit(self, message: InboundMessage) -> None:
        """Hand the hub a message read at one of the extension's endpoints.

        Returns once the hub has stored the message, or has recognized it as a copy of one it
        stored before; only then may the extension confirm it to its network (e.g. mark a Briar
        post read). Cancelling the call is safe: the message is stored or not, and submitting it
        again is de-duplicated by its transport id. The hub never calls back into the extension
        from here. A message for an endpoint the hub does not know is logged and dropped.
        """

    @abstractmethod
    async def heard(self, account: Account, endpoint: EndpointRef | None = None) -> None:
        """Tell the hub that the network just showed `account` to be there, at `endpoint` if it
        was heard at one: any packet from a radio counts, not only a message.

        The routing script reads it as `last_heard` (design.md §9.5). Submitted messages count
        without this call.
        """

    @abstractmethod
    async def retry_now(self, endpoint: EndpointRef, recipient: str | None = None) -> None:
        """Make the deliveries to `endpoint` that wait for a retry due now, only those to
        `recipient` if it is given: e.g. when a radio that missed messages is heard again.
        """

    @abstractmethod
    async def notify_admin(self, text: str, *, key: str | None = None) -> None:
        """Post an admin notice (design.md §2), e.g. a foreign Telegram group with its chat id.

        Notices with the same `key` (by default, the same text) are rate-limited together, so
        a repeated problem does not flood the admin. Never raises.
        """

    @abstractmethod
    def now(self) -> datetime:
        """The hub's clock: the current time, timezone-aware."""
