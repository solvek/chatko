"""The outbox worker: design.md §9.1, step 5, and the delivery rules of architecture.md §3.2."""

import asyncio
from collections import Counter, deque
from datetime import timedelta
from typing import ClassVar

import pytest
from pydantic import BaseModel

from chatko.application.installation import Installation
from chatko.application.outbox import OutboxSettings, OutboxWorker
from chatko.application.testing import FakeClock, InMemoryStore
from chatko.domain import Author, Delivery, DeliveryState, EndpointRef, Message, MessageId, Target
from chatko.extension_api import (
    Delivered,
    DeliveryReport,
    DeliveryResult,
    EndpointProvider,
    Extension,
    Failed,
    OutboundMessage,
    Retry,
)
from chatko.extension_api.testing import FakeConfig, FakeEndpointConfig, FakeHub
from tests.unit.application.rig import (
    ADA,
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    TOPOLOGY,
    settle,
)

HANG = object()
"""An answer: the call never returns."""

type Answer = DeliveryResult | Exception | object


class Scripted(Extension[FakeConfig], EndpointProvider[FakeEndpointConfig]):
    """An extension that answers deliveries as the test says, and records the calls."""

    type_name: ClassVar[str] = "scripted"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = FakeConfig
    endpoint_config_model: ClassVar[type[BaseModel]] = FakeEndpointConfig

    def __init__(self, instance: str) -> None:
        super().__init__(instance, FakeConfig(), FakeHub())
        self.answers: deque[Answer] = deque()
        self.otherwise: Answer = Delivered()
        self.gate = asyncio.Event()
        self.gate.set()
        self.calls: list[tuple[EndpointRef, OutboundMessage]] = []
        self.reports: list[DeliveryReport] = []
        self.report_error: Exception | None = None
        self._active: Counter[tuple[EndpointRef, str | None]] = Counter()
        self.most_active: Counter[tuple[EndpointRef, str | None]] = Counter()
        self.in_flight = 0

    def set_endpoints(self, endpoints: object) -> None:
        del endpoints

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        self.calls.append((endpoint, message))
        lane = (endpoint, message.recipient)
        self._active[lane] += 1
        self.most_active[lane] = max(self.most_active[lane], self._active[lane])
        self.in_flight += 1
        try:
            await self.gate.wait()
            answer = self.answers.popleft() if self.answers else self.otherwise
            if answer is HANG:
                await asyncio.Event().wait()
            if isinstance(answer, Exception):
                raise answer
            assert isinstance(answer, Delivered | Retry | Failed)
            return answer
        finally:
            self._active[lane] -= 1
            self.in_flight -= 1

    async def delivery_report(self, report: DeliveryReport) -> None:
        if self.report_error is not None:
            raise self.report_error
        self.reports.append(report)

    def texts(self) -> list[str]:
        return [message.text for _, message in self.calls]


class OutboxRig:
    """The worker with two scripted instances: `mesh` delivers, `tg` is where messages come from."""

    def __init__(
        self,
        settings: OutboxSettings | None = None,
        *,
        store: InMemoryStore | None = None,
        clock: FakeClock | None = None,
    ) -> None:
        self.clock = FakeClock() if clock is None else clock
        self.store = InMemoryStore() if store is None else store
        self.mesh = Scripted("mesh")
        self.tg = Scripted("tg")
        self.installation = Installation(TOPOLOGY, {"mesh": self.mesh, "tg": self.tg})
        self.settings = settings
        self.worker = self.new_worker()
        self._numbers = iter(range(1, 1000))

    def new_worker(self) -> OutboxWorker:
        return OutboxWorker(
            installation=lambda: self.installation,
            messages=self.store,
            outbox=self.store,
            clock=self.clock,
            settings=self.settings,
        )

    async def add(
        self,
        text: str,
        *destinations: tuple[EndpointRef, str | None],
        enqueue: bool = True,
    ) -> list[Delivery]:
        number = next(self._numbers)
        message = Message(
            MessageId(f"m{number}"), FAMILY_TG, f"t{number}", Author(ADA), text, self.clock.now()
        )
        deliveries = [
            Delivery.of(message, Target(endpoint), "Ada", self.clock.now(), recipient)
            for endpoint, recipient in destinations or [(FAMILY_CHANNEL, None)]
        ]
        assert await self.store.add(message, deliveries)
        if enqueue:
            self.worker.enqueue(deliveries)
        return deliveries

    def state(self, index: int = 0) -> Delivery:
        return self.store.deliveries[index]

    async def advance(self, delta: timedelta) -> None:
        self.clock.advance(delta)
        await settle()


@pytest.fixture
async def rig() -> OutboxRig:
    rig = OutboxRig()
    await rig.worker.start()
    return rig


async def test_a_due_delivery_is_delivered(rig: OutboxRig) -> None:
    await rig.add("Привіт")
    await settle()

    [(endpoint, message)] = rig.mesh.calls
    assert endpoint == FAMILY_CHANNEL
    assert message == OutboundMessage(MessageId("m1"), "Ada", "Привіт", rig.clock.now())
    assert rig.state().state is DeliveryState.DELIVERED
    assert rig.state().attempts == 1


async def test_the_source_extension_gets_a_report_when_a_delivery_ends(rig: OutboxRig) -> None:
    await rig.add("Привіт")
    await settle()

    assert rig.tg.reports == [DeliveryReport(FAMILY_TG, "t1", FAMILY_CHANNEL, None, Delivered())]
    assert not rig.mesh.reports


async def test_a_truncated_delivery_is_reported_as_such(rig: OutboxRig) -> None:
    rig.mesh.answers.append(Delivered(truncated=True))
    await rig.add("a long text")
    await settle()

    assert rig.state().truncated
    assert rig.tg.reports[0].result == Delivered(truncated=True)


async def test_each_recipient_gets_its_own_delivery(rig: OutboxRig) -> None:
    await rig.add("Привіт", (FAMILY_RADIO, "!a1"), (FAMILY_RADIO, "!b2"))
    await settle()

    assert [(e, m.recipient) for e, m in rig.mesh.calls] == [
        (FAMILY_RADIO, "!a1"),
        (FAMILY_RADIO, "!b2"),
    ]
    assert [r.recipient for r in rig.tg.reports] == ["!a1", "!b2"]


async def test_one_endpoint_gets_one_delivery_at_a_time_oldest_first(rig: OutboxRig) -> None:
    rig.mesh.gate.clear()
    for text in ["1", "2", "3"]:
        await rig.add(text)
    await settle()
    assert rig.mesh.texts() == ["1"]

    rig.mesh.gate.set()
    await settle()

    assert rig.mesh.texts() == ["1", "2", "3"]
    assert rig.mesh.most_active[FAMILY_CHANNEL, None] == 1


async def test_endpoints_and_recipients_are_served_concurrently(rig: OutboxRig) -> None:
    rig.mesh.gate.clear()
    await rig.add("x", (FAMILY_CHANNEL, None), (FAMILY_RADIO, "!a1"), (FAMILY_RADIO, "!b2"))
    await settle()

    assert rig.mesh.in_flight == 3


async def test_a_retry_holds_back_newer_messages_until_it_is_due(rig: OutboxRig) -> None:
    rig.mesh.answers.append(Retry("busy", after=timedelta(seconds=30)))
    await rig.add("1")
    await rig.add("2")
    await settle()
    assert rig.mesh.texts() == ["1"]
    assert rig.state().due_at == rig.clock.now() + timedelta(seconds=30)
    assert rig.state().last_error == "busy"

    await rig.advance(timedelta(seconds=29))
    assert rig.mesh.texts() == ["1"]

    await rig.advance(timedelta(seconds=1))
    assert rig.mesh.texts() == ["1", "1", "2"]
    assert [m.attempt for _, m in rig.mesh.calls] == [1, 2, 1]


async def test_a_recipient_that_is_away_holds_up_nobody_else(rig: OutboxRig) -> None:
    rig.mesh.answers.append(Retry("no ACK", after=timedelta(hours=1)))
    await rig.add("1", (FAMILY_RADIO, "!a1"))
    await rig.add("2", (FAMILY_RADIO, "!b2"), (FAMILY_CHANNEL, None))
    await settle()

    assert sorted(rig.mesh.texts()) == ["1", "2", "2"]


async def test_without_a_time_of_its_own_a_retry_backs_off_exponentially() -> None:
    rig = OutboxRig(OutboxSettings(first_retry=timedelta(seconds=10), backoff_factor=2))
    await rig.worker.start()
    rig.mesh.otherwise = Retry("down")
    await rig.add("1")
    await settle()

    waits = []
    for _ in range(3):
        waits.append(rig.state().due_at - rig.clock.now())
        await rig.advance(waits[-1])

    assert waits == [timedelta(seconds=s) for s in (10, 20, 40)]


def test_the_backoff_stops_growing_at_the_longest_wait() -> None:
    settings = OutboxSettings(
        first_retry=timedelta(seconds=10), backoff_factor=3, longest_retry=timedelta(minutes=1)
    )

    assert [settings.backoff(n).total_seconds() for n in range(1, 5)] == [10, 30, 60, 60]


@pytest.mark.parametrize(
    "settings",
    [
        {"first_retry": timedelta(0)},
        {"first_retry": timedelta(hours=2)},
        {"backoff_factor": 0.5},
        {"give_up_after": timedelta(0)},
        {"call_timeout": timedelta(0)},
        {"stop_grace": timedelta(seconds=-1)},
    ],
)
def test_settings_must_make_sense(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"must|cannot"):
        OutboxSettings(**settings)  # type: ignore[arg-type]


async def test_retry_now_ends_the_wait(rig: OutboxRig) -> None:
    rig.mesh.answers.append(Retry("no ACK", after=timedelta(hours=1)))
    await rig.add("1", (FAMILY_RADIO, "!a1"))
    await settle()

    rig.worker.retry_now(FAMILY_RADIO)
    await settle()

    assert rig.mesh.texts() == ["1", "1"]
    assert rig.state().state is DeliveryState.DELIVERED


async def test_retry_now_for_a_recipient_leaves_the_others_waiting(rig: OutboxRig) -> None:
    rig.mesh.answers.extend([Retry("away", after=timedelta(hours=1))] * 2)
    await rig.add("1", (FAMILY_RADIO, "!a1"), (FAMILY_RADIO, "!b2"))
    await settle()

    rig.worker.retry_now(FAMILY_RADIO, "!b2")
    rig.worker.retry_now(FAMILY_CHANNEL)
    await settle()

    assert [m.recipient for _, m in rig.mesh.calls] == ["!a1", "!b2", "!b2"]


async def test_failed_is_final_and_the_next_message_follows(rig: OutboxRig) -> None:
    rig.mesh.answers.append(Failed("chat is gone"))
    await rig.add("1")
    await rig.add("2")
    await settle()

    assert rig.mesh.texts() == ["1", "2"]
    assert rig.state().state is DeliveryState.FAILED
    assert rig.state().last_error == "chat is gone"
    assert rig.tg.reports[0].result == Failed("chat is gone")


async def test_an_exception_counts_as_a_retry_and_is_logged(
    rig: OutboxRig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.mesh.answers.append(ConnectionError("reset"))
    await rig.add("1")
    await settle()

    assert rig.state().state is DeliveryState.PENDING
    assert rig.state().last_error == "ConnectionError: reset"
    assert "mesh raised while delivering to mesh/family.channel" in caplog.text


async def test_a_call_that_takes_too_long_counts_as_a_retry() -> None:
    rig = OutboxRig(OutboxSettings(call_timeout=timedelta(milliseconds=10)))
    await rig.worker.start()
    rig.mesh.answers.append(HANG)
    await rig.add("1")
    await asyncio.sleep(0.05)
    await settle()

    assert rig.state().state is DeliveryState.PENDING
    assert rig.state().last_error == "no answer within 0:00:00.010000"


async def test_it_gives_up_on_a_message_that_is_too_old() -> None:
    rig = OutboxRig(
        OutboxSettings(first_retry=timedelta(minutes=10), give_up_after=timedelta(minutes=30))
    )
    await rig.worker.start()
    rig.mesh.otherwise = Retry("down")
    await rig.add("1")
    await rig.add("2")
    await settle()

    for _ in range(3):  # attempts after 10 and 30 minutes
        await rig.advance(timedelta(minutes=10))

    assert rig.state().state is DeliveryState.FAILED
    assert rig.state().attempts == 3
    assert rig.state().last_error == "gave up: down"
    assert rig.tg.reports[0].result == Failed("gave up: down")
    assert rig.state(1).attempts == 1


async def test_an_instance_that_is_not_running_counts_as_a_retry(rig: OutboxRig) -> None:
    rig.installation = Installation(TOPOLOGY, {"tg": rig.tg})
    await rig.add("1")
    await settle()
    assert rig.state().last_error == "the extension instance 'mesh' is not running"

    rig.installation = Installation(TOPOLOGY, {"tg": rig.tg, "mesh": rig.mesh})
    await rig.advance(timedelta(minutes=1))

    assert rig.state().state is DeliveryState.DELIVERED


async def test_no_report_without_the_source_instance(rig: OutboxRig) -> None:
    rig.installation = Installation(TOPOLOGY, {"mesh": rig.mesh})
    await rig.add("1")
    await settle()

    assert rig.state().state is DeliveryState.DELIVERED


async def test_a_delivery_to_an_endpoint_no_longer_in_the_config_fails(rig: OutboxRig) -> None:
    gone = EndpointRef("mesh", "gone.channel")
    await rig.add("1", (gone, None))
    await settle()

    assert rig.state().state is DeliveryState.FAILED
    assert rig.state().last_error == "gone.channel is no longer in the config"
    assert not rig.mesh.calls


async def test_the_report_goes_to_the_source_only_if_it_runs_when_the_delivery_ends(
    rig: OutboxRig,
) -> None:
    rig.mesh.gate.clear()
    await rig.add("1")
    await settle()
    rig.installation = Installation(TOPOLOGY, {"mesh": rig.mesh, "tg": rig.tg}, running={"mesh"})
    rig.mesh.gate.set()
    await settle()

    assert rig.state().state is DeliveryState.DELIVERED
    assert not rig.tg.reports


async def test_finish_attempts_waits_for_the_instances_deliveries_in_progress(
    rig: OutboxRig,
) -> None:
    rig.mesh.gate.clear()
    await rig.add("1")
    await settle()
    assert rig.mesh.in_flight == 1

    finishing = asyncio.create_task(rig.worker.finish_attempts("mesh"))
    await settle()
    assert not finishing.done()
    await rig.worker.finish_attempts("tg")  # nothing in progress there

    rig.mesh.gate.set()
    await asyncio.wait_for(finishing, 1)
    assert rig.state().state is DeliveryState.DELIVERED


async def test_a_failing_report_is_logged_and_ignored(
    rig: OutboxRig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.tg.report_error = RuntimeError("boom")
    await rig.add("1")
    await rig.add("2")
    await settle()

    assert rig.mesh.texts() == ["1", "2"]
    assert "tg raised on a delivery report" in caplog.text


class TestRestarts:
    async def test_a_new_worker_resumes_the_pending_deliveries(self) -> None:
        rig = OutboxRig()
        await rig.worker.start()
        rig.mesh.answers.append(Retry("busy", after=timedelta(minutes=1)))
        await rig.add("1")
        await rig.add("2")
        await settle()
        await rig.worker.stop()

        rig.worker = rig.new_worker()
        await rig.worker.start()
        await settle()
        assert rig.mesh.texts() == ["1"]
        await rig.advance(timedelta(minutes=1))

        assert rig.mesh.texts() == ["1", "1", "2"]
        assert rig.mesh.calls[1][1].attempt == 2

    async def test_an_attempt_cut_short_by_a_crash_is_repeated_as_a_repeat(self) -> None:
        rig = OutboxRig(OutboxSettings(stop_grace=timedelta(0)))
        await rig.worker.start()
        rig.mesh.gate.clear()
        await rig.add("1")
        await settle()
        await rig.worker.stop()  # the call in progress is cancelled
        assert rig.state().attempts == 1

        rig.mesh.gate.set()
        rig.worker = rig.new_worker()
        await rig.worker.start()
        await settle()

        assert [m.attempt for _, m in rig.mesh.calls] == [1, 2]
        assert rig.state().state is DeliveryState.DELIVERED

    async def test_stop_lets_the_delivery_in_progress_end(self) -> None:
        rig = OutboxRig()
        await rig.worker.start()
        rig.mesh.gate.clear()
        await rig.add("1")
        await rig.add("2")
        await settle()

        stopping = asyncio.create_task(rig.worker.stop())
        await settle()
        assert not stopping.done()
        rig.mesh.gate.set()
        await stopping

        assert rig.mesh.texts() == ["1"]
        assert [d.state for d in rig.store.deliveries] == [
            DeliveryState.DELIVERED,
            DeliveryState.PENDING,
        ]

    async def test_deliveries_stored_while_stopped_wait_for_start(self) -> None:
        rig = OutboxRig()
        await rig.add("1")
        await settle()
        assert not rig.mesh.calls

        await rig.worker.start()
        await settle()

        assert rig.mesh.texts() == ["1"]

    async def test_deliveries_queued_while_starting_follow_the_loaded_ones(self) -> None:
        class SlowStore(InMemoryStore):
            def __init__(self) -> None:
                super().__init__()
                self.loading = asyncio.Event()
                self.gate = asyncio.Event()

            async def pending(self) -> list[Delivery]:
                found = await super().pending()
                self.loading.set()
                await self.gate.wait()
                return found

        store = SlowStore()
        rig = OutboxRig(store=store)
        first = await rig.add("1")
        starting = asyncio.create_task(rig.worker.start())
        await store.loading.wait()
        rig.worker.enqueue(first)  # already loaded: not delivered twice
        await rig.add("2")
        store.gate.set()
        await starting
        await settle()

        assert rig.mesh.texts() == ["1", "2"]

    async def test_a_worker_starts_once(self, rig: OutboxRig) -> None:
        with pytest.raises(RuntimeError, match="running already"):
            await rig.worker.start()

    async def test_a_failed_start_can_be_repeated(self) -> None:
        class BrokenStore(InMemoryStore):
            broken = True

            async def pending(self) -> list[Delivery]:
                if self.broken:
                    raise OSError("locked")
                return await super().pending()

        store = BrokenStore()
        rig = OutboxRig(store=store)
        with pytest.raises(OSError, match="locked"):
            await rig.worker.start()

        store.broken = False
        await rig.worker.start()

    async def test_stop_without_start_is_safe(self) -> None:
        await OutboxRig().worker.stop()


async def test_a_repository_error_is_logged_and_the_lane_tries_again_later(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FlakyStore(InMemoryStore):
        failures = 1

        async def save(self, delivery: Delivery) -> None:
            if self.failures:
                self.failures -= 1
                raise OSError("disk I/O error")
            await super().save(delivery)

    rig = OutboxRig(OutboxSettings(first_retry=timedelta(seconds=10)), store=FlakyStore())
    await rig.worker.start()
    await rig.add("1")
    await settle()
    assert "the delivery of m1 to mesh/family.channel broke" in caplog.text
    assert not rig.mesh.calls

    await rig.advance(timedelta(seconds=10))

    assert rig.state().state is DeliveryState.DELIVERED


async def test_only_pending_deliveries_are_queued(rig: OutboxRig) -> None:
    [delivery] = await rig.add("1", enqueue=False)

    rig.worker.enqueue([delivery.begin_attempt().delivered()])
    await settle()

    assert not rig.mesh.calls


async def test_finished_lanes_are_let_go(rig: OutboxRig) -> None:
    await rig.add("1")
    await settle()
    await rig.add("2")
    await settle()

    assert rig.mesh.texts() == ["1", "2"]
    assert rig.clock.sleepers == 0
