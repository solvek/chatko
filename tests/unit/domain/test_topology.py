import pytest

from chatko.domain import Account, AccountKey, DomainError, EndpointRef, Group, Person, Topology

TG_FAMILY = EndpointRef("tg", "chat:-1001")
BRIAR_FAMILY = EndpointRef("briar", "group:fam")
TG_STREET = EndpointRef("tg", "chat:-1002")
MESH_DM = EndpointRef("kyiv", "dm:1")
LONGFAST = EndpointRef("kyiv", "channel:LongFast")
OWNER = EndpointRef("tg", "chat:123")
FAMILY = Group("family", (TG_FAMILY, BRIAR_FAMILY, MESH_DM))
STREET = Group("street", (TG_STREET,))
NAT_TG = AccountKey("telegram", "111")
NAT_MESH = AccountKey("meshtastic", "!a1b2c3d4")
NATA = Person("NatAda", frozenset({NAT_TG, NAT_MESH}))


def topology() -> Topology:
    return Topology(
        groups=(FAMILY, STREET),
        sources={"longfast": LONGFAST, "owner": OWNER},
        people=(NATA,),
    )


def test_groups_by_name() -> None:
    assert topology().group("street") == STREET


def test_unknown_group_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="no group 'x'"):
        topology().group("x")


def test_group_of_a_leg() -> None:
    assert topology().group_of(MESH_DM) == FAMILY


def test_group_of_a_source_or_unknown_endpoint_is_none() -> None:
    assert topology().group_of(LONGFAST) is None
    assert topology().group_of(EndpointRef("tg", "chat:9")) is None


def test_sources_by_name() -> None:
    assert topology().source("longfast") == LONGFAST


def test_unknown_source_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="no source 'x'"):
        topology().source("x")


def test_sources_cannot_be_changed_through_the_topology() -> None:
    sources = {"owner": OWNER}
    topo = Topology(sources=sources)
    sources["other"] = LONGFAST

    assert "other" not in topo.sources
    with pytest.raises(TypeError):
        topo.sources["other"] = LONGFAST  # type: ignore[index]


def test_endpoints_are_all_legs_and_sources() -> None:
    topo = topology()

    assert topo.endpoints == {TG_FAMILY, BRIAR_FAMILY, MESH_DM, TG_STREET, LONGFAST, OWNER}
    assert topo.has_endpoint(OWNER)
    assert topo.has_endpoint(TG_STREET)
    assert not topo.has_endpoint(EndpointRef("tg", "chat:9"))


def test_person_of_an_account() -> None:
    assert topology().person_of(NAT_MESH) == NATA
    assert topology().person_of(AccountKey("telegram", "222")) is None


def test_author_of_a_listed_account_is_the_person() -> None:
    account = Account(NAT_TG, "Наталія")

    author = topology().author_of(account)

    assert author.account == account
    assert author.person == NATA


def test_author_of_an_unlisted_account_has_no_person() -> None:
    assert topology().author_of(Account(AccountKey("telegram", "222"))).person is None


def test_empty_topology_is_valid() -> None:
    assert Topology().endpoints == frozenset()


def test_topologies_with_the_same_content_are_equal() -> None:
    assert topology() == topology()


def test_group_names_are_unique() -> None:
    with pytest.raises(DomainError, match="defined twice"):
        Topology(groups=(FAMILY, Group("family", (TG_STREET,))))


def test_endpoint_is_a_leg_of_one_group_only() -> None:
    with pytest.raises(DomainError, match="leg of both 'family' and 'street'"):
        Topology(groups=(FAMILY, Group("street", (TG_STREET, BRIAR_FAMILY))))


def test_source_is_not_a_leg() -> None:
    with pytest.raises(DomainError, match="also a leg of group 'family'"):
        Topology(groups=(FAMILY,), sources={"fam": TG_FAMILY})


def test_two_sources_are_not_the_same_endpoint() -> None:
    with pytest.raises(DomainError, match="the same"):
        Topology(sources={"a": OWNER, "b": OWNER})


def test_source_needs_a_name() -> None:
    with pytest.raises(DomainError, match="needs a name"):
        Topology(sources={" ": OWNER})


def test_person_labels_are_unique() -> None:
    with pytest.raises(DomainError, match="defined twice"):
        Topology(people=(NATA, Person("NatAda", frozenset({AccountKey("telegram", "2")}))))


def test_account_belongs_to_one_person_only() -> None:
    with pytest.raises(DomainError, match="belongs to both 'NatAda' and 'Nata2'"):
        Topology(people=(NATA, Person("Nata2", frozenset({NAT_MESH}))))
