"""The outbox worker: delivers the outbox rows through the extensions (docs/design.md §9.1)."""

import asyncio
import logging
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any

from chatko.application.installation import Installation, InstallationSource
from chatko.application.ports import Clock, MessageRepository, OutboxRepository
from chatko.domain import Delivery, DeliveryState, EndpointRef, Message, MessageId
from chatko.extension_api import (
    Delivered,
    DeliveryReport,
    DeliveryResult,
    Failed,
    OutboundMessage,
    Retry,
)

_log = logging.getLogger("chatko.outbox")

type _LaneKey = tuple[EndpointRef, str | None]


@dataclass(frozen=True, slots=True)
class OutboxSettings:
    """When the worker tries again, gives up and stops waiting.

    After a `Retry` without its own `after`, the next attempt is due after `first_retry`, then
    each wait is `backoff_factor` times longer, up to `longest_retry`. A delivery whose message
    arrived `give_up_after` ago or earlier fails at its next `Retry`. A `deliver` or
    `delivery_report` call that takes longer than `call_timeout` counts as a `Retry`. `stop`
    gives the deliveries in progress `stop_grace` to end.
    """

    first_retry: timedelta = timedelta(seconds=10)
    backoff_factor: float = 2.0
    longest_retry: timedelta = timedelta(hours=1)
    give_up_after: timedelta = timedelta(days=3)
    call_timeout: timedelta = timedelta(minutes=2)
    stop_grace: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if self.first_retry <= timedelta(0) or self.longest_retry < self.first_retry:
            raise ValueError("retry waits must be positive, the first no longer than the longest")
        if self.backoff_factor < 1:
            raise ValueError("the backoff factor must be at least 1")
        if min(self.give_up_after, self.call_timeout) <= timedelta(0):
            raise ValueError("the give-up time and the call timeout must be positive")
        if self.stop_grace < timedelta(0):
            raise ValueError("the stop grace cannot be negative")

    def backoff(self, attempts: int) -> timedelta:
        """The wait after the `attempts`-th attempt answered `Retry` without a time of its own."""
        longest = self.longest_retry / self.first_retry
        exponent = max(attempts - 1, 0)
        if self.backoff_factor > 1:  # Beyond this the wait is the longest anyway: no overflow.
            exponent = min(exponent, math.ceil(math.log(longest, self.backoff_factor)))
        return self.first_retry * min(self.backoff_factor**exponent, longest)


class _State(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()


@dataclass(eq=False)
class _Lane:
    """The pending deliveries to one endpoint (and recipient), oldest first, and the task that
    delivers them one at a time."""

    key: _LaneKey
    queue: deque[Delivery] = field(default_factory=deque)
    ids: set[MessageId] = field(default_factory=set)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    attempt: asyncio.Task[Delivery] | None = None
    """The attempt in progress."""
    task: asyncio.Task[None] | None = None


class OutboxWorker:
    """Delivers the pending outbox rows, each endpoint and recipient on its own (a lane).

    In a lane one delivery is attempted at a time, oldest first, once it is due; a delivery that
    waits for its retry holds back the newer ones, so they never overtake each other. Lanes run
    concurrently. Every attempt is saved before the extension is called and its outcome after, so
    a restart resumes where the worker stopped (the attempt in progress may be repeated). When a
    delivery ends, the extension of the message's source gets a `DeliveryReport`.
    """

    def __init__(
        self,
        *,
        installation: InstallationSource,
        messages: MessageRepository,
        outbox: OutboxRepository,
        clock: Clock,
        settings: OutboxSettings | None = None,
    ) -> None:
        self._installation = installation
        self._messages = messages
        self._outbox = outbox
        self._clock = clock
        self._settings = OutboxSettings() if settings is None else settings
        self._state = _State.STOPPED
        self._lanes: dict[_LaneKey, _Lane] = {}
        self._early: list[Delivery] = []
        self._reporting: dict[asyncio.Task[Any], str] = {}
        """The attempts that are reporting to an instance now, with its name."""

    async def start(self) -> None:
        """Load the pending deliveries and start delivering them."""
        if self._state is not _State.STOPPED:
            raise RuntimeError("the outbox worker is running already")
        self._state = _State.STARTING
        try:
            pending = await self._outbox.pending()
        except BaseException:
            self._state = _State.STOPPED
            raise
        self._state = _State.RUNNING
        self.enqueue([*pending, *self._early])
        self._early.clear()
        _log.info("started with %d pending deliveries", len(pending))

    def enqueue(self, deliveries: Sequence[Delivery]) -> None:
        """Deliver these too, after those queued before them. They must be stored already:
        while the worker is stopped they are left to the next `start`."""
        if self._state is _State.STARTING:
            self._early.extend(deliveries)
            return
        if self._state is not _State.RUNNING:
            return
        for delivery in deliveries:
            if delivery.state is not DeliveryState.PENDING:
                continue
            key = (delivery.endpoint, delivery.recipient)
            lane = self._lanes.get(key)
            if lane is None:
                lane = self._lanes[key] = _Lane(key)
                lane.task = asyncio.create_task(self._run(lane), name=f"chatko.outbox.{key}")
            if delivery.message_id not in lane.ids:
                lane.ids.add(delivery.message_id)
                lane.queue.append(delivery)

    def retry_now(self, endpoint: EndpointRef, recipient: str | None = None) -> None:
        """Make the deliveries to `endpoint` (only those to `recipient`, if given) that wait for
        a retry due now."""
        for (lane_endpoint, lane_recipient), lane in self._lanes.items():
            if lane_endpoint == endpoint and recipient in (None, lane_recipient):
                lane.wake.set()

    async def finish_attempts(self, instance: str) -> None:
        """Wait until the calls in progress into `instance` have ended, the attempts to its
        endpoints and the delivery reports to it, so that it can be stopped (architecture.md
        §3.1). Take it out of the installation's running instances first, or new calls follow."""
        calls: set[asyncio.Task[Any]] = {
            lane.attempt
            for lane in self._lanes.values()
            if lane.key[0].instance == instance and lane.attempt is not None
        }
        calls.update(task for task, to in self._reporting.items() if to == instance)
        if calls:
            await asyncio.wait(calls)

    async def stop(self) -> None:
        """Stop delivering. The deliveries in progress get `stop_grace` to end."""
        self._state = _State.STOPPED
        self._early.clear()
        lanes = list(self._lanes.values())
        self._lanes.clear()
        tasks = [lane.task for lane in lanes if lane.task is not None]
        for lane in lanes:
            if lane.attempt is None and lane.task is not None:
                lane.task.cancel()
        if not tasks:
            return
        _, unfinished = await asyncio.wait(tasks, timeout=self._settings.stop_grace.total_seconds())
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.wait(unfinished)

    async def _run(self, lane: _Lane) -> None:
        try:
            while self._state is _State.RUNNING and lane.queue:
                head = lane.queue[0]
                if head.due_at > self._clock.now() and not lane.wake.is_set():
                    await self._sleep(lane, head.due_at)
                    continue
                lane.wake.clear()
                lane.attempt = asyncio.ensure_future(self._attempt(head))
                try:
                    attempted = await lane.attempt
                except Exception:
                    _log.exception(
                        "the delivery of %s to %s broke", head.message_id, head.destination
                    )
                    await self._sleep(lane, self._clock.now() + self._settings.first_retry)
                    continue
                finally:
                    lane.attempt = None
                if attempted.state is DeliveryState.PENDING:
                    lane.queue[0] = attempted
                else:
                    lane.queue.popleft()
                    lane.ids.discard(attempted.message_id)
        finally:
            if self._lanes.get(lane.key) is lane and not lane.queue:
                del self._lanes[lane.key]

    async def _sleep(self, lane: _Lane, until: datetime) -> None:
        """Wait until `until`, or until `retry_now` wakes the lane."""
        sleeper = asyncio.ensure_future(self._clock.sleep_until(until))
        waker = asyncio.ensure_future(lane.wake.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            waker.cancel()

    async def _attempt(self, pending: Delivery) -> Delivery:
        installation = self._installation()
        message = await self._messages.get(pending.message_id)
        delivery = pending.begin_attempt()
        await self._outbox.save(delivery)
        result = await self._deliver(installation, message, delivery)
        ended = self._apply(delivery, message, result)
        await self._outbox.save(ended)
        if ended.state is DeliveryState.PENDING:
            _log.info(
                "delivery of %s to %s: retry at %s (%s)",
                ended.message_id,
                ended.destination,
                ended.due_at,
                ended.last_error,
            )
        else:
            _log.log(
                logging.INFO if ended.state is DeliveryState.DELIVERED else logging.WARNING,
                "delivery of %s to %s %s after %d attempts%s",
                ended.message_id,
                ended.destination,
                ended.state,
                ended.attempts,
                f": {ended.last_error}" if ended.last_error else "",
            )
            await self._report(message, ended)
        return ended

    async def _deliver(
        self, installation: Installation, message: Message, delivery: Delivery
    ) -> DeliveryResult:
        if not installation.topology.has_endpoint(delivery.endpoint):
            return Failed(f"{delivery.endpoint.name} is no longer in the config")
        instance = delivery.endpoint.instance
        provider = installation.provider(instance)
        if provider is None:
            return Retry(f"the extension instance {instance!r} is not running")
        outbound = OutboundMessage(
            message.id,
            delivery.author_label,
            delivery.text,
            message.received_at,
            message.attachments,
            delivery.recipient,
            delivery.attempts,
        )
        try:
            async with asyncio.timeout(self._settings.call_timeout.total_seconds()):
                return await provider.deliver(delivery.endpoint, outbound)
        except TimeoutError:
            return Retry(f"no answer within {self._settings.call_timeout}")
        except Exception as error:
            _log.exception("%s raised while delivering to %s", instance, delivery.destination)
            return Retry(f"{type(error).__name__}: {error}")

    def _apply(self, delivery: Delivery, message: Message, result: DeliveryResult) -> Delivery:
        if isinstance(result, Delivered):
            return delivery.delivered(truncated=result.truncated)
        if isinstance(result, Failed):
            return delivery.failed(result.reason)
        now = self._clock.now()
        if now - message.received_at >= self._settings.give_up_after:
            return delivery.failed(f"gave up: {result.reason}")
        wait = self._settings.backoff(delivery.attempts) if result.after is None else result.after
        return delivery.retry(now + wait, result.reason)

    async def _report(self, message: Message, ended: Delivery) -> None:
        source = message.endpoint
        provider = self._installation().provider(source.instance)
        if provider is None:
            return
        result = (
            Delivered(ended.truncated)
            if ended.state is DeliveryState.DELIVERED
            else Failed(ended.last_error or "")
        )
        report = DeliveryReport(
            source, message.transport_id, ended.endpoint, ended.recipient, result
        )
        attempt = asyncio.current_task()
        assert attempt is not None  # noqa: S101 - an attempt is a task (`_run`)
        self._reporting[attempt] = source.instance
        try:
            async with asyncio.timeout(self._settings.call_timeout.total_seconds()):
                await provider.delivery_report(report)
        except Exception:
            _log.exception("%s raised on a delivery report", source.instance)
        finally:
            del self._reporting[attempt]
