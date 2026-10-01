"""The real clock and id generator (ports of `chatko.application.ports`)."""

import asyncio
import uuid
from datetime import UTC, datetime

from chatko.domain import MessageId


class SystemClock:
    """The system's time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        await asyncio.sleep(max((when - self.now()).total_seconds(), 0))


class RandomIds:
    """Random message ids: 32 hex digits."""

    def new_message_id(self) -> MessageId:
        return MessageId(uuid.uuid4().hex)
