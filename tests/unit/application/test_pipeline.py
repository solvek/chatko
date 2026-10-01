"""The inbound pipeline: design.md §9.1, steps 2–4, and the invariants of §9.3 it applies."""

import asyncio
from collections.abc import Sequence
from datetime import timedelta

import pytest

from chatko.application.pipeline import InboundPipeline, Outcome
from chatko.application.routing import DefaultRouter
from chatko.application.testing import InMemoryStore, SequentialIds
from chatko.domain import (
    Account,
    Attachment,
    AttachmentKind,
    Delivery,
    EndpointRef,
    Message,
    Target,
)
from chatko.extension_api import InboundMessage
from chatko.routing_api import RoutedMessage, RoutingContext, mirror, to_endpoint
from tests.unit.application.rig import (
    ADA,
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    LONGFAST,
    NAT,
    OWNER,
    STREET_CHANNEL,
    STREET_TG,
    Rig,
)


class ScriptRouter:
    """A router whose targets and labels a test sets; it records what it was called with."""

    def __init__(self, targets: Sequence[Target] | None = None, label: str | None = None) -> None:
        self.targets = targets
        self.fixed_label = label
        self.routed: list[RoutedMessage] = []
        self.contexts: list[RoutingContext] = []
        self.labelled: list[Target] = []

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]:
        self.routed.append(msg)
        self.contexts.append(ctx)
        return mirror(msg, ctx) if self.targets is None else self.targets

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        self.labelled.append(target)
        return self.fixed_label or f"L-{msg.author.account.key.external_id}"


def inbound(
    endpoint: EndpointRef = FAMILY_TG,
    transport_id: str = "t1",
    author: Account = ADA,
    text: str = "Привіт",
    attachments: tuple[Attachment, ...] = (),
) -> InboundMessage:
    return InboundMessage(endpoint, transport_id, author, text, attachments)


def destinations(deliveries: Sequence[Delivery]) -> list[str]:
    return [delivery.destination for delivery in deliveries]


async def test_a_site_message_goes_to_every_other_site_of_its_group() -> None:
    rig = Rig()

    assert await rig.pipeline.submit(inbound()) is Outcome.ROUTED

    assert destinations(rig.store.deliveries) == [
        "mesh/family.radio:!a1",
        "mesh/family.radio:!b2",
        "mesh/family.channel",
    ]


async def test_the_message_is_stored_with_its_author_and_time() -> None:
    rig = Rig()
    photo = (Attachment(AttachmentKind.PHOTO),)

    await rig.pipeline.submit(inbound(text="caption", attachments=photo))

    [message] = rig.store.messages.values()
    assert message.endpoint == FAMILY_TG
    assert message.transport_id == "t1"
    assert message.author.account == ADA
    assert message.author.person is None
    assert message.text == "caption"
    assert message.attachments == photo
    assert message.received_at == rig.clock.now()


async def test_each_delivery_has_the_label_text_and_due_time() -> None:
    rig = Rig()

    await rig.pipeline.submit(inbound(text="Привіт"))

    delivery = rig.store.deliveries[-1]
    assert delivery.author_label == "AdaLov"
    assert delivery.text == "Привіт"
    assert delivery.due_at == rig.clock.now()
    assert delivery.attempts == 0


async def test_an_account_of_a_person_is_signed_with_the_persons_label() -> None:
    rig = Rig()

    await rig.pipeline.submit(inbound(author=NAT))

    [message] = rig.store.messages.values()
    assert message.author.person is not None
    assert {delivery.author_label for delivery in rig.store.deliveries} == {"NatAda"}


async def test_a_copy_with_the_same_transport_id_is_dropped() -> None:
    rig = Rig()
    await rig.pipeline.submit(inbound(text="first"))

    assert await rig.pipeline.submit(inbound(text="again")) is Outcome.COPY

    assert len(rig.store.messages) == 1
    assert len(rig.store.deliveries) == 3


async def test_the_same_transport_id_at_another_endpoint_is_another_message() -> None:
    rig = Rig()
    await rig.pipeline.submit(inbound(FAMILY_TG))

    assert await rig.pipeline.submit(inbound(STREET_TG)) is Outcome.ROUTED

    assert len(rig.store.messages) == 2


async def test_a_message_from_an_unknown_endpoint_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig()
    unknown = EndpointRef("tg", "gone.tg")

    assert await rig.pipeline.submit(inbound(unknown)) is Outcome.UNKNOWN_ENDPOINT

    assert not rig.store.messages
    assert "tg/gone.tg" in caplog.text


async def test_a_source_goes_nowhere_by_default_but_is_stored() -> None:
    rig = Rig()

    assert await rig.pipeline.submit(inbound(LONGFAST)) is Outcome.ROUTED

    assert len(rig.store.messages) == 1
    assert not rig.store.deliveries


async def test_the_router_gets_the_message_with_its_group_and_the_installation() -> None:
    router = ScriptRouter()
    rig = Rig(router=router)

    await rig.pipeline.submit(inbound(FAMILY_CHANNEL, author=NAT))

    [msg] = router.routed
    [ctx] = router.contexts
    assert msg.endpoint == FAMILY_CHANNEL
    assert msg.group is not None
    assert msg.group.name == "family"
    assert ctx.now == rig.clock.now()
    assert ctx.extension_type(FAMILY_TG) == "fake"
    assert ctx.recipients(FAMILY_RADIO) == ("!a1", "!b2")
    assert ctx.recipients(FAMILY_TG) == ()
    assert ctx.person_of(NAT.key) is not None


async def test_a_target_may_narrow_the_recipients() -> None:
    rig = Rig(router=ScriptRouter([to_endpoint(FAMILY_RADIO, recipients=["!b2"])]))

    await rig.pipeline.submit(inbound())

    assert destinations(rig.store.deliveries) == ["mesh/family.radio:!b2"]


async def test_a_target_with_no_recipients_delivers_nothing_there() -> None:
    rig = Rig(router=ScriptRouter([to_endpoint(FAMILY_RADIO, recipients=[])]))

    await rig.pipeline.submit(inbound())

    assert not rig.store.deliveries


async def test_a_target_may_replace_the_text_and_the_label() -> None:
    router = ScriptRouter([to_endpoint(STREET_TG, text="#street news", label="Base")])
    rig = Rig(router=router)

    await rig.pipeline.submit(inbound(text="news"))

    [delivery] = rig.store.deliveries
    assert (delivery.text, delivery.author_label) == ("#street news", "Base")
    assert not router.labelled


async def test_the_label_hook_is_asked_once_per_target_not_per_recipient() -> None:
    router = ScriptRouter([to_endpoint(FAMILY_RADIO), to_endpoint(OWNER)], label="~Ada")
    rig = Rig(router=router)

    await rig.pipeline.submit(inbound())

    assert router.labelled == [Target(FAMILY_RADIO), Target(OWNER)]
    assert {delivery.author_label for delivery in rig.store.deliveries} == {"~Ada"}


async def test_the_invariants_apply_to_whatever_the_router_returns() -> None:
    rig = Rig(
        router=ScriptRouter(
            [
                to_endpoint(FAMILY_TG),  # back to the source
                to_endpoint(STREET_TG, text="first"),
                to_endpoint(STREET_TG, text="second"),  # the same endpoint again
                to_endpoint(EndpointRef("tg", "gone.tg")),  # not in the config
            ]
        )
    )

    await rig.pipeline.submit(inbound())

    [delivery] = rig.store.deliveries
    assert (delivery.endpoint, delivery.text) == (STREET_TG, "first")


async def test_submitted_messages_count_as_heard() -> None:
    rig = Rig()

    await rig.pipeline.submit(inbound(FAMILY_CHANNEL, author=NAT))

    assert rig.history.last_heard(NAT.key, FAMILY_CHANNEL) == rig.clock.now()
    assert rig.history.last_heard(NAT.key, None) == rig.clock.now()


async def test_seen_counts_the_messages_stored_before_only() -> None:
    seen: list[bool] = []

    class SeenRouter(ScriptRouter):
        def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]:
            seen.append(ctx.seen(msg.fingerprint, within=timedelta(minutes=5)))
            return []

    rig = Rig(router=SeenRouter())
    await rig.pipeline.submit(inbound(FAMILY_TG, "t1"))
    rig.clock.advance(timedelta(minutes=1))
    await rig.pipeline.submit(inbound(STREET_TG, "t2"))

    assert seen == [False, True]


class TestFingerprintDeduplication:
    """A message that another path already brought in is routed only once, where enabled."""

    async def test_a_second_copy_by_another_path_is_stored_but_not_routed(self) -> None:
        rig = Rig(fingerprint_dedup={STREET_CHANNEL: timedelta(hours=1)})
        await rig.pipeline.submit(inbound(STREET_TG, "t1", text="Hello  World"))
        rig.clock.advance(timedelta(minutes=59))

        outcome = await rig.pipeline.submit(inbound(STREET_CHANNEL, "p9", text="hello world"))

        assert outcome is Outcome.NOT_ROUTED
        assert len(rig.store.messages) == 2
        assert destinations(rig.store.deliveries) == ["mesh/street.channel"]

    async def test_a_copy_after_the_window_is_routed(self) -> None:
        rig = Rig(fingerprint_dedup={STREET_CHANNEL: timedelta(hours=1)})
        await rig.pipeline.submit(inbound(STREET_TG, "t1"))
        rig.clock.advance(timedelta(hours=1, seconds=1))

        assert await rig.pipeline.submit(inbound(STREET_CHANNEL, "p9")) is Outcome.ROUTED

    async def test_endpoints_without_it_route_every_copy(self) -> None:
        rig = Rig(fingerprint_dedup={STREET_CHANNEL: timedelta(hours=1)})
        await rig.pipeline.submit(inbound(STREET_CHANNEL, "p1"))

        assert await rig.pipeline.submit(inbound(STREET_TG, "t2")) is Outcome.ROUTED

    async def test_another_author_is_another_message(self) -> None:
        rig = Rig(fingerprint_dedup={STREET_CHANNEL: timedelta(hours=1)})
        await rig.pipeline.submit(inbound(STREET_TG, "t1", author=NAT))

        assert await rig.pipeline.submit(inbound(STREET_CHANNEL, "p9")) is Outcome.ROUTED


async def test_messages_are_handled_in_the_order_they_were_submitted() -> None:
    rig = Rig()

    await asyncio.gather(
        *(rig.pipeline.submit(inbound(transport_id=f"t{n}", text=f"#{n}")) for n in range(5))
    )

    texts = [d.text for d in rig.store.deliveries if d.endpoint == FAMILY_CHANNEL]
    assert texts == ["#0", "#1", "#2", "#3", "#4"]


async def test_the_stored_deliveries_go_to_the_outbox_worker() -> None:
    class Queue:
        def __init__(self) -> None:
            self.queued: list[Delivery] = []

        def enqueue(self, deliveries: Sequence[Delivery]) -> None:
            self.queued.extend(deliveries)

    rig, queue = Rig(), Queue()
    pipeline = InboundPipeline(
        installation=rig.current,
        messages=rig.store,
        outbox=queue,
        router=DefaultRouter(),
        history=rig.history,
        clock=rig.clock,
        ids=SequentialIds(),
    )

    await pipeline.submit(inbound())

    assert queue.queued == rig.store.deliveries
    assert len(queue.queued) == 3


class GatedStore(InMemoryStore):
    """Holds `add` until the test opens the gate."""

    def __init__(self) -> None:
        super().__init__()
        self.adding = asyncio.Event()
        self.gate = asyncio.Event()

    async def add(self, message: Message, deliveries: Sequence[Delivery]) -> bool:
        self.adding.set()
        await self.gate.wait()
        return await super().add(message, deliveries)


async def test_cancelling_a_submission_does_not_cancel_storing_it() -> None:
    store = GatedStore()
    rig = Rig(store=store)
    submission = asyncio.create_task(rig.pipeline.submit(inbound()))
    await store.adding.wait()

    submission.cancel()
    store.gate.set()
    with pytest.raises(asyncio.CancelledError):
        await submission
    await asyncio.sleep(0)

    assert len(store.messages) == 1
    assert rig.history.last_heard(ADA.key, FAMILY_TG) is not None


async def test_a_copy_that_lost_the_race_to_the_store_is_dropped() -> None:
    class RacingStore(InMemoryStore):
        async def contains(self, endpoint: EndpointRef, transport_id: str) -> bool:
            return False  # as if the other copy was stored after this check

    rig = Rig(store=RacingStore())
    await rig.pipeline.submit(inbound())

    assert await rig.pipeline.submit(inbound()) is Outcome.COPY
    assert len(rig.store.deliveries) == 3


async def test_a_failing_store_fails_the_submission_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenStore(InMemoryStore):
        async def add(self, message: Message, deliveries: Sequence[Delivery]) -> bool:
            raise OSError("disk full")

    rig = Rig(store=BrokenStore())

    with pytest.raises(OSError, match="disk full"):
        await rig.pipeline.submit(inbound())
    assert "handling a submitted message failed" in caplog.text
