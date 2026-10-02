"""The core's `HubContext` (architecture.md §3.3)."""

from datetime import timedelta

import pytest

from chatko.application.testing import RecordedNotice, RecordingNotices
from chatko.extension_api import InboundMessage
from tests.unit.application.rig import (
    ADA,
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    NAT,
    Rig,
    settle,
)


async def test_submit_hands_the_message_to_the_pipeline() -> None:
    rig = Rig()

    await rig.hub("telegram").submit(InboundMessage(FAMILY_TG, "t1", ADA, "Привіт"))

    assert len(rig.store.messages) == 1


async def test_an_instance_cannot_submit_for_another_instances_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig()

    await rig.hub("telegram").submit(InboundMessage(FAMILY_CHANNEL, "t1", ADA, "Привіт"))

    assert not rig.store.messages
    assert "telegram told the hub a message at mesh/family.channel" in caplog.text


async def test_heard_at_an_endpoint_is_last_heard_there_and_anywhere() -> None:
    rig = Rig()

    await rig.hub("mesh").heard(NAT, FAMILY_CHANNEL)

    assert rig.history.last_heard(NAT.key, FAMILY_CHANNEL) == rig.clock.now()
    assert rig.history.last_heard(NAT.key, None) == rig.clock.now()
    assert rig.history.last_heard(NAT.key, FAMILY_RADIO) is None


async def test_heard_without_an_endpoint_is_last_heard_anywhere() -> None:
    rig = Rig()

    await rig.hub("mesh").heard(NAT)

    assert rig.history.last_heard(NAT.key, None) == rig.clock.now()


async def test_heard_at_another_instances_endpoint_is_ignored() -> None:
    rig = Rig()

    await rig.hub("telegram").heard(NAT, FAMILY_CHANNEL)

    assert rig.history.last_heard(NAT.key, None) is None


async def test_retry_now_ends_the_wait_of_the_deliveries_there() -> None:
    rig = Rig()
    await rig.start()
    mesh = rig.extensions["mesh"]
    mesh.network.online = False
    await rig.hub("telegram").submit(InboundMessage(FAMILY_TG, "t1", ADA, "Привіт"))
    await settle()
    assert not rig.posted(FAMILY_RADIO, recipient="!a1")

    mesh.network.online = True
    await rig.hub("mesh").retry_now(FAMILY_RADIO, "!a1")
    await settle()

    assert rig.posted(FAMILY_RADIO, recipient="!a1") == ["AdaLov: Привіт"]
    assert not rig.posted(FAMILY_RADIO, recipient="!b2")
    await rig.stop()


async def test_retry_now_for_another_instances_endpoint_is_ignored() -> None:
    rig = Rig()
    await rig.start()
    rig.extensions["mesh"].network.online = False
    await rig.hub("telegram").submit(InboundMessage(FAMILY_TG, "t1", ADA, "Привіт"))
    await settle()
    rig.extensions["mesh"].network.online = True

    await rig.hub("telegram").retry_now(FAMILY_CHANNEL)
    await settle()

    assert not rig.posted(FAMILY_CHANNEL)
    await rig.stop()


async def test_admin_notices_are_rate_limited_per_instance_and_key() -> None:
    rig = Rig()

    await rig.hub("telegram").notify_admin("a foreign chat: -100", key="foreign:-100")
    await rig.hub("mesh").notify_admin("key mismatch")

    assert rig.notices.notices == [
        RecordedNotice("a foreign chat: -100", "telegram:foreign:-100"),
        RecordedNotice("key mismatch", "mesh:key mismatch"),
    ]


async def test_a_lost_admin_notice_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenNotices(RecordingNotices):
        async def notify(self, text: str, *, key: str) -> None:
            raise ConnectionError(text)

    rig = Rig()
    rig.notices = BrokenNotices()

    await rig.hub("telegram").notify_admin("hello")

    assert "an admin notice from telegram was lost: hello" in caplog.text


def test_now_is_the_hubs_clock() -> None:
    rig = Rig()
    rig.clock.advance(timedelta(hours=1))

    assert rig.hub("telegram").now() == rig.clock.now()
