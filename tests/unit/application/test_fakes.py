"""The in-memory fakes of the application's ports."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from chatko.application.testing import FakeClock, InMemoryStore, SequentialIds
from chatko.domain import Author, Delivery, EndpointRef, Message, MessageId, Target
from tests.unit.application.rig import ADA, FAMILY_CHANNEL, FAMILY_TG, STREET_TG

T0 = FakeClock().now()


def message(number: int = 1, endpoint: EndpointRef = FAMILY_TG) -> Message:
    return Message(MessageId(f"m{number}"), endpoint, f"t{number}", Author(ADA), "x", T0)


def delivery(msg: Message, endpoint: EndpointRef = FAMILY_CHANNEL) -> Delivery:
    return Delivery.of(msg, Target(endpoint), "Ada", T0)


class TestFakeClock:
    def test_it_stands_still_until_moved(self) -> None:
        clock = FakeClock()
        clock.advance(timedelta(minutes=1))

        assert clock.now() == T0 + timedelta(minutes=1)

    async def test_a_sleeper_wakes_when_its_time_comes(self) -> None:
        clock = FakeClock()
        sleeper = asyncio.create_task(clock.sleep_until(T0 + timedelta(seconds=10)))
        await asyncio.sleep(0)
        assert clock.sleepers == 1

        clock.advance(timedelta(seconds=9))
        await asyncio.sleep(0)
        assert not sleeper.done()

        clock.advance(timedelta(seconds=1))
        await sleeper
        assert clock.sleepers == 0

    async def test_a_time_that_has_passed_returns_at_once(self) -> None:
        await FakeClock().sleep_until(T0)

    async def test_a_cancelled_sleeper_is_let_go(self) -> None:
        clock = FakeClock()
        sleeper = asyncio.create_task(clock.sleep_until(T0 + timedelta(seconds=10)))
        await asyncio.sleep(0)
        sleeper.cancel()
        await asyncio.sleep(0)

        clock.advance(timedelta(seconds=10))

        assert clock.sleepers == 0

    def test_it_does_not_go_back(self) -> None:
        with pytest.raises(ValueError, match="back"):
            FakeClock().advance(timedelta(seconds=-1))

    def test_its_time_is_timezone_aware(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            FakeClock(datetime(2026, 10, 1))

    def test_its_default_time_is_utc(self) -> None:
        assert FakeClock().now().tzinfo is UTC


def test_sequential_ids_count_up() -> None:
    ids = SequentialIds()

    assert [ids.new_message_id(), ids.new_message_id()] == ["m1", "m2"]


class TestInMemoryStore:
    async def test_a_message_is_stored_with_its_deliveries(self) -> None:
        store = InMemoryStore()
        msg = message()

        assert await store.add(msg, [delivery(msg)])

        assert await store.get(msg.id) == msg
        assert await store.contains(FAMILY_TG, "t1")
        assert not await store.contains(STREET_TG, "t1")
        assert await store.pending() == [delivery(msg)]

    async def test_a_copy_stores_nothing(self) -> None:
        store = InMemoryStore()
        await store.add(message(1), [])
        copy = Message(MessageId("m2"), FAMILY_TG, "t1", Author(ADA), "x", T0)

        assert not await store.add(copy, [delivery(copy)])
        assert not store.deliveries

    async def test_saving_replaces_a_delivery_and_finished_ones_are_not_pending(self) -> None:
        store = InMemoryStore()
        msg = message()
        await store.add(msg, [delivery(msg), delivery(msg, STREET_TG)])

        await store.save(delivery(msg).begin_attempt().delivered())

        assert await store.pending() == [delivery(msg, STREET_TG)]

    async def test_an_unknown_message_or_delivery_is_a_key_error(self) -> None:
        store = InMemoryStore()

        with pytest.raises(KeyError, match="no message m1"):
            await store.get(MessageId("m1"))
        with pytest.raises(KeyError, match="no delivery of m1"):
            await store.save(delivery(message()))

    async def test_it_refuses_what_a_database_would(self) -> None:
        store = InMemoryStore()
        first, other = message(1), message(2)
        await store.add(first, [])

        with pytest.raises(ValueError, match="stored already"):
            await store.add(Message(first.id, STREET_TG, "t9", Author(ADA), "x", T0), [])
        with pytest.raises(ValueError, match="repeat a destination"):
            await store.add(other, [delivery(other), delivery(other)])
        with pytest.raises(ValueError, match="not one of m2"):
            await store.add(other, [delivery(first)])
