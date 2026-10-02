from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

import pytest

from chatko.application.installation import Installation
from chatko.application.notifier import NOTICE_LABEL, NOTICE_SOURCE, AdminNotifier
from chatko.application.testing import FakeClock, InMemoryStore, SequentialIds
from chatko.domain import Delivery, EndpointRef, Message, Topology
from chatko.extension_api.testing import FakeConfig, FakeEndpointConfig, FakeExtension, FakeHub

OWNER = EndpointRef("telegram", "owner")
RADIO = EndpointRef("mesh", "radio")


class Queue:
    def __init__(self) -> None:
        self.queued: list[Delivery] = []

    def enqueue(self, deliveries: Sequence[Delivery]) -> None:
        self.queued.extend(deliveries)


class Rig:
    def __init__(self, target: EndpointRef | None = OWNER) -> None:
        self.store = InMemoryStore()
        self.queue = Queue()
        self.clock = FakeClock()
        mesh = FakeExtension("mesh", FakeConfig(), FakeHub())
        mesh.set_endpoints({RADIO: FakeEndpointConfig(place="r", recipients=("!a1", "!b2"))})
        topology = Topology(sources={"owner": OWNER, "radio": RADIO})
        self.installation = Installation(topology, {"mesh": mesh}, admin_endpoint=target)
        self.notifier = AdminNotifier(
            installation=lambda: self.installation,
            messages=self.store,
            outbox=self.queue,
            clock=self.clock,
            ids=SequentialIds(),
            min_interval=timedelta(minutes=10),
        )


async def test_a_notice_is_stored_as_a_message_with_a_delivery_to_the_admin_endpoint() -> None:
    rig = Rig()

    await rig.notifier.notify("The config was not loaded", key="config:load")

    [delivery] = rig.queue.queued
    assert delivery.endpoint == OWNER
    assert delivery.recipient is None
    assert delivery.author_label == NOTICE_LABEL
    assert delivery.text == "The config was not loaded"
    message = await rig.store.get(delivery.message_id)
    assert message.endpoint == NOTICE_SOURCE
    assert message.received_at == rig.clock.now()
    assert [d.message_id for d in await rig.store.pending()] == [message.id]


async def test_an_endpoint_with_recipients_gets_a_delivery_for_each() -> None:
    rig = Rig(RADIO)

    await rig.notifier.notify("hello", key="k")

    assert [d.recipient for d in rig.queue.queued] == ["!a1", "!b2"]


async def test_the_admin_endpoint_is_the_current_snapshots() -> None:
    rig = Rig()
    rig.installation = replace(rig.installation, admin_endpoint=RADIO)

    await rig.notifier.notify("moved", key="k")

    assert {d.endpoint for d in rig.queue.queued} == {RADIO}


async def test_without_an_admin_endpoint_the_notice_is_only_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig(None)

    await rig.notifier.notify("lonely", key="k")

    assert rig.queue.queued == []
    assert "lonely" in caplog.text


async def test_notices_of_a_key_are_held_back_within_the_interval() -> None:
    rig = Rig()

    await rig.notifier.notify("first", key="k")
    rig.clock.advance(timedelta(minutes=9))
    await rig.notifier.notify("second", key="k")
    await rig.notifier.notify("third", key="k")
    await rig.notifier.notify("other key", key="other")

    assert [d.text for d in rig.queue.queued] == ["first", "other key"]


async def test_after_the_interval_the_next_notice_says_how_many_were_held_back() -> None:
    rig = Rig()
    await rig.notifier.notify("first", key="k")
    await rig.notifier.notify("again", key="k")
    await rig.notifier.notify("and again", key="k")

    rig.clock.advance(timedelta(minutes=10))
    await rig.notifier.notify("later", key="k")
    rig.clock.advance(timedelta(minutes=10))
    await rig.notifier.notify("much later", key="k")

    assert [d.text for d in rig.queue.queued] == [
        "first",
        "later\n(2 similar notices were held back before)",
        "much later",
    ]


async def test_a_failing_store_does_not_raise() -> None:
    rig = Rig()

    async def broken(message: Message, deliveries: Sequence[Delivery]) -> bool:
        raise RuntimeError("disk full")

    rig.store.add = broken  # type: ignore[method-assign]

    await rig.notifier.notify("x", key="k")

    assert rig.queue.queued == []
