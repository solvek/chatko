"""The hub's in-memory routing history: last heard and recent fingerprints."""

from datetime import UTC, datetime, timedelta

import pytest

from chatko.application.history import HubHistory
from chatko.domain import AccountKey, EndpointRef, fingerprint

NODE = AccountKey("meshtastic", "!a1b2c3d4")
CHANNEL = EndpointRef("mesh", "family.channel")
RADIO = EndpointRef("mesh", "family.radio")
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FP = fingerprint("NatAda", "Привіт")


def test_an_account_never_heard_has_no_time() -> None:
    assert HubHistory().last_heard(NODE, None) is None


def test_the_latest_time_is_kept_per_endpoint_and_anywhere() -> None:
    history = HubHistory()
    history.hear(NODE, T0 + timedelta(minutes=5), CHANNEL)
    history.hear(NODE, T0, CHANNEL)  # told late: an older time does not win
    history.hear(NODE, T0 + timedelta(minutes=1), RADIO)

    assert history.last_heard(NODE, CHANNEL) == T0 + timedelta(minutes=5)
    assert history.last_heard(NODE, RADIO) == T0 + timedelta(minutes=1)
    assert history.last_heard(NODE, None) == T0 + timedelta(minutes=5)


def test_heard_anywhere_is_not_heard_at_an_endpoint() -> None:
    history = HubHistory()
    history.hear(NODE, T0)

    assert history.last_heard(NODE, None) == T0
    assert history.last_heard(NODE, CHANNEL) is None


def test_seen_since_compares_with_the_latest_arrival() -> None:
    history = HubHistory()
    history.see(FP, T0)
    history.see(FP, T0 + timedelta(minutes=10))

    assert history.seen_since(FP, T0 + timedelta(minutes=10))
    assert not history.seen_since(FP, T0 + timedelta(minutes=11))
    assert not history.seen_since(fingerprint("NatAda", "інше"), T0)


def test_fingerprints_older_than_the_retention_are_forgotten() -> None:
    history = HubHistory(retention=timedelta(hours=1))
    history.see(FP, T0)
    other = fingerprint("AdaLov", "hi")

    history.see(other, T0 + timedelta(hours=1, seconds=1))

    assert not history.seen_since(FP, T0 - timedelta(days=1))
    assert history.seen_since(other, T0)


def test_a_fingerprint_seen_again_is_kept_by_its_latest_arrival() -> None:
    history = HubHistory(retention=timedelta(hours=1))
    history.see(FP, T0)
    history.see(FP, T0 + timedelta(minutes=50))

    history.see(fingerprint("AdaLov", "hi"), T0 + timedelta(minutes=70))

    assert history.seen_since(FP, T0 + timedelta(minutes=50))


def test_the_retention_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        HubHistory(retention=timedelta(0))
