"""The default router: `mirror` and `default_label`, as without a routing script (§9.4)."""

from chatko.application.routing import DefaultRouter
from chatko.application.testing import FakeClock
from chatko.domain import Author, EndpointRef, Message, MessageId, Target
from chatko.routing_api import RoutedMessage, RoutingContext
from tests.unit.application.rig import (
    FAMILY_CHANNEL,
    FAMILY_RADIO,
    FAMILY_TG,
    LONGFAST,
    NAT,
    NATADA,
    TOPOLOGY,
)

NOW = FakeClock().now()
CTX = RoutingContext(TOPOLOGY, now=NOW)


def routed(endpoint: EndpointRef = FAMILY_TG) -> RoutedMessage:
    message = Message(MessageId("m1"), endpoint, "t1", Author(NAT, NATADA), "Привіт", NOW)
    return RoutedMessage(message, TOPOLOGY.group_of(endpoint))


def test_a_site_goes_to_the_other_sites_of_its_group() -> None:
    assert DefaultRouter().route(routed(), CTX) == [Target(FAMILY_RADIO), Target(FAMILY_CHANNEL)]


def test_a_source_goes_nowhere() -> None:
    assert DefaultRouter().route(routed(LONGFAST), CTX) == []


def test_the_label_is_the_default_one() -> None:
    assert DefaultRouter().label(routed(), Target(FAMILY_RADIO), CTX) == "NatAda"
