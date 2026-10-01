"""A message's whole way through the hub's application layer, with `FakeExtension` networks:
design.md §9.1 from the extension's submission to the delivery report."""

from chatko.domain import DeliveryState, EndpointRef
from tests.unit.application.rig import (
    ADA,
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    NAT,
    PLACES,
    STREET_CHANNEL,
    STREET_TG,
    Rig,
    settle,
)


def place(endpoint: EndpointRef) -> str:
    return PLACES[endpoint].place


async def test_a_post_reaches_every_other_site_and_each_recipient() -> None:
    rig = Rig()
    await rig.start()

    rig.networks["tg"].post(place(FAMILY_TG), NAT, "Привіт усім")
    await settle()

    assert rig.posted(FAMILY_CHANNEL) == ["NatAda: Привіт усім"]
    assert rig.posted(FAMILY_RADIO, recipient="!a1") == ["NatAda: Привіт усім"]
    assert rig.posted(FAMILY_RADIO, recipient="!b2") == ["NatAda: Привіт усім"]
    assert not rig.posted(STREET_TG)
    await rig.stop()


async def test_the_hubs_own_posts_never_come_back_in() -> None:
    # §9.3: the hub's own posts never reach the router (FakeExtension drops them, step 1).
    rig = Rig()
    await rig.start()

    rig.networks["mesh"].post(place(STREET_CHANNEL), ADA, "hi")
    await settle()

    assert rig.posted(STREET_TG) == ["AdaLov: hi"]
    assert len(rig.store.messages) == 1
    assert not rig.posted(STREET_CHANNEL)
    await rig.stop()


async def test_a_post_handed_over_twice_is_relayed_once() -> None:
    rig = Rig()
    await rig.start()
    network = rig.networks["mesh"]

    post = network.post(place(STREET_CHANNEL), ADA, "hi")
    network.hand_over(post)  # e.g. heard through a second gateway
    await settle()

    assert rig.posted(STREET_TG) == ["AdaLov: hi"]
    await rig.stop()


async def test_the_source_extension_learns_how_each_delivery_ended() -> None:
    rig = Rig()
    await rig.start()

    rig.networks["tg"].post(place(FAMILY_TG), NAT, "Привіт")
    await settle()

    reports = rig.extensions["tg"].reports
    assert {(r.target, r.recipient) for r in reports} == {
        (FAMILY_CHANNEL, None),
        (FAMILY_RADIO, "!a1"),
        (FAMILY_RADIO, "!b2"),
    }
    assert {r.source for r in reports} == {FAMILY_TG}
    await rig.stop()


async def test_messages_wait_while_a_network_is_down_and_keep_their_order() -> None:
    rig = Rig()
    await rig.start()
    rig.networks["mesh"].online = False

    for text in ["1", "2", "3"]:
        rig.networks["tg"].post(place(STREET_TG), NAT, text)
        await settle()
    rig.networks["mesh"].online = True
    await rig.advance_past_retries()

    assert rig.posted(STREET_CHANNEL) == ["NatAda: 1", "NatAda: 2", "NatAda: 3"]
    await rig.stop()


async def test_pending_deliveries_survive_a_restart() -> None:
    rig = Rig()
    await rig.start()
    rig.networks["mesh"].online = False
    rig.networks["tg"].post(place(STREET_TG), NAT, "1")
    await settle()
    await rig.stop()

    rig.networks["mesh"].online = True
    restarted = Rig(store=rig.store, clock=rig.clock, networks=rig.networks)
    await restarted.start()
    await restarted.advance_past_retries()

    assert restarted.posted(STREET_CHANNEL) == ["NatAda: 1"]
    assert {d.state for d in rig.store.deliveries} == {DeliveryState.DELIVERED}
    await restarted.stop()
