"""The real clock and id generator."""

from datetime import UTC, timedelta

from chatko.infrastructure.clock import RandomIds, SystemClock


def test_the_system_clock_is_utc() -> None:
    assert SystemClock().now().tzinfo is UTC


async def test_sleeping_until_a_past_time_returns_at_once() -> None:
    clock = SystemClock()

    await clock.sleep_until(clock.now() - timedelta(hours=1))


async def test_sleeping_until_a_near_time() -> None:
    clock = SystemClock()
    until = clock.now() + timedelta(milliseconds=5)

    await clock.sleep_until(until)

    assert clock.now() >= until


def test_random_ids_differ() -> None:
    ids = RandomIds()

    assert len({ids.new_message_id() for _ in range(100)}) == 100
