import pytest

from chatko.domain import DomainError, EndpointRef, Group

TG = EndpointRef("telegram", "chat:-100")
BRIAR = EndpointRef("briar", "group:abc")
MESH = EndpointRef("kyiv", "channel:family")


def test_endpoint_is_written_as_instance_and_name() -> None:
    assert str(TG) == "telegram/chat:-100"


def test_endpoints_with_the_same_instance_and_name_are_equal() -> None:
    assert EndpointRef("telegram", "chat:-100") == TG
    assert len({EndpointRef("telegram", "chat:-100"), TG}) == 1


@pytest.mark.parametrize(
    ("instance", "name"), [("", "x"), (" ", "x"), ("telegram", ""), ("telegram", "  ")]
)
def test_endpoint_needs_an_instance_and_a_name(instance: str, name: str) -> None:
    with pytest.raises(DomainError):
        EndpointRef(instance, name)


def test_group_knows_its_sites() -> None:
    group = Group("family", (TG, BRIAR))

    assert group.has_site(TG)
    assert not group.has_site(MESH)


def test_other_sites_are_all_sites_but_the_given_one_in_config_order() -> None:
    group = Group("family", (TG, BRIAR, MESH))

    assert group.other_sites(BRIAR) == (TG, MESH)


def test_other_sites_of_an_endpoint_outside_the_group_are_all_sites() -> None:
    assert Group("family", (TG, BRIAR)).other_sites(MESH) == (TG, BRIAR)


def test_group_needs_a_name() -> None:
    with pytest.raises(DomainError, match="name"):
        Group(" ", (TG,))


def test_group_needs_a_site() -> None:
    with pytest.raises(DomainError, match="no sites"):
        Group("family", ())


def test_group_cannot_list_a_site_twice() -> None:
    with pytest.raises(DomainError, match="twice"):
        Group("family", (TG, BRIAR, TG))
