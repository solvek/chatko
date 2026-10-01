"""A fake of the hub that records what an extension hands it."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from chatko.domain import Account, EndpointRef
from chatko.extension_api.hub import HubContext
from chatko.extension_api.messages import InboundMessage

DEFAULT_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Heard:
    account: Account
    endpoint: EndpointRef | None


@dataclass(frozen=True, slots=True)
class RetryRequest:
    endpoint: EndpointRef
    recipient: str | None


@dataclass(frozen=True, slots=True)
class Notice:
    text: str
    key: str | None


class FakeHub(HubContext):
    """Records every call, in order. Its clock stands still at `time` until a test changes it."""

    def __init__(self, time: datetime = DEFAULT_TIME, *, wait_timeout: float = 2.0) -> None:
        self.time = time
        self.wait_timeout = wait_timeout
        self.submitted: list[InboundMessage] = []
        self.heard_accounts: list[Heard] = []
        self.retries: list[RetryRequest] = []
        self.notices: list[Notice] = []
        self._submissions = asyncio.Condition()

    async def submit(self, message: InboundMessage) -> None:
        async with self._submissions:
            self.submitted.append(message)
            self._submissions.notify_all()

    async def heard(self, account: Account, endpoint: EndpointRef | None = None) -> None:
        self.heard_accounts.append(Heard(account, endpoint))

    async def retry_now(self, endpoint: EndpointRef, recipient: str | None = None) -> None:
        self.retries.append(RetryRequest(endpoint, recipient))

    async def notify_admin(self, text: str, *, key: str | None = None) -> None:
        self.notices.append(Notice(text, key))

    def now(self) -> datetime:
        return self.time

    async def wait_for_submissions(self, count: int) -> list[InboundMessage]:
        """Wait until at least `count` messages have been submitted, and return all of them.

        Gives up with `TimeoutError` after `wait_timeout` seconds.
        """
        try:
            async with asyncio.timeout(self.wait_timeout), self._submissions:
                await self._submissions.wait_for(lambda: len(self.submitted) >= count)
        except TimeoutError:
            raise TimeoutError(
                f"{len(self.submitted)} of {count} messages were submitted "
                f"within {self.wait_timeout} s"
            ) from None
        return list(self.submitted)
