"""The routing invariants (design.md §9.3): examples, and properties that hold whatever a routing
script returns."""

import asyncio
from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from chatko.application.invariants import Destination, RoutingInvariants
from chatko.domain import EndpointRef, Target
from chatko.extension_api import InboundMessage
from chatko.routing_api import RoutedMessage, RoutingContext, to_endpoint
from tests.unit.application.rig import (
    ADA,
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    LONGFAST,
    OWNER,
    PLACES,
    RECIPIENTS,
    STREET_CHANNEL,
    STREET_TG,
    TOPOLOGY,
    Rig,
)

UNKNOWN = EndpointRef("telegram", "gone.telegram")
KNOWN = sorted(PLACES)
RECIPIENTS_OF = {FAMILY_RADIO: RECIPIENTS}


def apply(
    source: EndpointRef, targets: Sequence[Target], from_recipient: str | None = None
) -> list[Destination]:
    return RoutingInvariants(TOPOLOGY, RECIPIENTS_OF).apply(source, targets, from_recipient)


def slots(destinations: Sequence[Destination]) -> list[tuple[EndpointRef, str | None]]:
    return [(d.endpoint, d.recipient) for d in destinations]


def test_targets_pass_in_their_order() -> None:
    targets = [to_endpoint(STREET_TG), to_endpoint(FAMILY_CHANNEL)]

    assert apply(FAMILY_TG, targets) == [Destination(t) for t in targets]


def test_nothing_goes_back_to_the_source() -> None:
    assert apply(FAMILY_TG, [to_endpoint(FAMILY_TG)]) == []


def test_nothing_goes_back_to_a_source_with_recipients_if_the_sender_is_unknown() -> None:
    assert apply(FAMILY_RADIO, [to_endpoint(FAMILY_RADIO)]) == []


def test_a_recipients_message_goes_to_the_other_recipients_of_its_endpoint() -> None:
    target = to_endpoint(FAMILY_RADIO)

    assert apply(FAMILY_RADIO, [target], "!a1") == [Destination(target, "!b2")]


def test_a_recipient_that_is_no_recipient_of_the_source_changes_nothing_elsewhere() -> None:
    target = to_endpoint(FAMILY_TG)

    assert apply(STREET_TG, [target], "!a1") == [Destination(target)]


def test_an_unknown_endpoint_is_dropped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    assert apply(FAMILY_TG, [to_endpoint(UNKNOWN)]) == []
    assert "telegram/gone.telegram" in caplog.text


def test_the_first_target_for_an_endpoint_wins() -> None:
    first = to_endpoint(STREET_TG, text="first")

    assert apply(FAMILY_TG, [first, to_endpoint(STREET_TG, text="second")]) == [Destination(first)]


def test_an_endpoint_with_recipients_gets_one_destination_per_recipient() -> None:
    target = to_endpoint(FAMILY_RADIO)

    assert apply(FAMILY_TG, [target]) == [Destination(target, "!a1"), Destination(target, "!b2")]


def test_a_target_may_narrow_the_recipients() -> None:
    target = to_endpoint(FAMILY_RADIO, recipients=["!b2", "!zz"])

    assert apply(FAMILY_TG, [target]) == [Destination(target, "!b2")]


def test_each_recipient_gets_the_message_once_from_the_first_target_that_includes_it() -> None:
    only_b = to_endpoint(FAMILY_RADIO, recipients=["!b2"], text="b")
    everyone = to_endpoint(FAMILY_RADIO, text="all")

    assert apply(FAMILY_TG, [only_b, everyone]) == [
        Destination(only_b, "!b2"),
        Destination(everyone, "!a1"),
    ]


def test_recipients_mean_nothing_for_an_endpoint_without_them() -> None:
    target = to_endpoint(STREET_TG, recipients=["!a1"])

    assert apply(FAMILY_TG, [target]) == [Destination(target)]


# Properties: whatever a script returns, the invariants hold.

endpoints = st.sampled_from([*KNOWN, UNKNOWN])
targets = st.builds(
    Target,
    endpoint=endpoints,
    text=st.none() | st.sampled_from(["a", "b"]),
    label=st.none() | st.sampled_from(["X", "Y"]),
    recipients=st.none() | st.frozensets(st.sampled_from([*RECIPIENTS, "!zz"])),
)
target_lists = st.lists(targets, max_size=12)


senders = st.none() | st.sampled_from([*RECIPIENTS, "!zz"])


def covers(
    target: Target,
    source: EndpointRef,
    sender: str | None,
    slot: tuple[EndpointRef, str | None],
) -> bool:
    endpoint, recipient = slot
    if target.endpoint != endpoint or endpoint == UNKNOWN:
        return False
    if endpoint == source and (sender is None or recipient in (None, sender)):
        return False
    names = RECIPIENTS_OF.get(endpoint, ())
    return recipient is None if not names else recipient in names and target.includes(recipient)


@given(st.sampled_from(KNOWN), target_lists, senders)
def test_nothing_goes_back_to_the_sender_or_to_an_unknown_endpoint(
    source: EndpointRef, ts: list[Target], sender: str | None
) -> None:
    for destination in apply(source, ts, sender):
        if destination.endpoint == source:
            assert sender is not None
            assert destination.recipient not in (None, sender)
        assert TOPOLOGY.has_endpoint(destination.endpoint)


@given(st.sampled_from(KNOWN), target_lists, senders)
def test_each_endpoint_and_recipient_gets_at_most_one(
    source: EndpointRef, ts: list[Target], sender: str | None
) -> None:
    found = slots(apply(source, ts, sender))

    assert len(found) == len(set(found))


@given(st.sampled_from(KNOWN), target_lists, senders)
def test_recipients_are_the_endpoints_own_and_the_targets_choice(
    source: EndpointRef, ts: list[Target], sender: str | None
) -> None:
    for destination in apply(source, ts, sender):
        names = RECIPIENTS_OF.get(destination.endpoint, ())
        if names:
            assert destination.recipient in names
            assert destination.target.includes(destination.recipient)
        else:
            assert destination.recipient is None


@given(st.sampled_from(KNOWN), target_lists, senders)
def test_every_allowed_destination_comes_from_the_first_target_that_covers_it(
    source: EndpointRef, ts: list[Target], sender: str | None
) -> None:
    found = {(d.endpoint, d.recipient): d.target for d in apply(source, ts, sender)}
    every_slot = [(e, r) for e in [*KNOWN, UNKNOWN] for r in RECIPIENTS_OF.get(e, (None,))]

    for slot in every_slot:
        first = next((t for t in ts if covers(t, source, sender, slot)), None)
        assert found.get(slot) == first


class RandomRouter:
    def __init__(self, targets: Sequence[Target]) -> None:
        self.targets = targets

    def route(self, msg: RoutedMessage, ctx: RoutingContext) -> Sequence[Target]:
        return self.targets

    def label(self, msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:
        return "L"


@given(st.sampled_from([FAMILY_TG, FAMILY_RADIO, LONGFAST, OWNER, STREET_CHANNEL]), target_lists)
def test_the_pipeline_stores_no_echo_and_no_duplicate_whatever_the_script_returns(
    source: EndpointRef, ts: list[Target]
) -> None:
    sender = "!a1" if source == FAMILY_RADIO else None
    rig = Rig(router=RandomRouter(ts))

    asyncio.run(
        rig.pipeline.submit(InboundMessage(source, "t1", ADA, "Привіт", from_recipient=sender))
    )

    keys = [(d.endpoint, d.recipient) for d in rig.store.deliveries]
    assert len(keys) == len(set(keys))
    assert (source, sender) not in keys
    assert all(TOPOLOGY.has_endpoint(d.endpoint) for d in rig.store.deliveries)


def test_sites_of_another_group_are_valid_targets() -> None:
    assert slots(apply(FAMILY_TG, [to_endpoint(STREET_CHANNEL)])) == [(STREET_CHANNEL, None)]
