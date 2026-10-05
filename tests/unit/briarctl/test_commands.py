"""Every command of design.md §7.5 against `FakeBriarClient`: what it does and what it prints."""

import pytest

from briarctl.api import RefusedError, UnreachableError
from briarctl.testing import OWN_LINK, FakeBriarClient
from tests.unit.briarctl.harness import TOKEN, briarctl


@pytest.fixture
def fake() -> FakeBriarClient:
    return FakeBriarClient()


# link


def test_link_prints_the_hubs_link(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "link")

    assert (result.code, result.lines) == (0, [OWN_LINK])


def test_link_in_json(fake: FakeBriarClient) -> None:
    assert briarctl(fake, "link", "--json").json == {"link": OWN_LINK}


# contact add / list / remove


def test_contact_add_starts_adding_the_contact(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    assert result.code == 0
    assert "Adding Nat" in result.out
    assert fake.calls == [("add_contact", "briar://abc", "Nat")]
    assert [p.alias for p in fake.pending_contacts()] == ["Nat"]


def test_contact_add_has_a_default_alias(fake: FakeBriarClient) -> None:
    briarctl(fake, "contact", "add", "briar://abc")

    assert fake.calls == [("add_contact", "briar://abc", "contact")]


def test_contact_add_refuses_a_link_that_is_not_one(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "contact", "add", "https://example.org")

    assert result.code == 1
    assert result.err == "briarctl: that is not a valid briar:// link\n"
    assert result.out == ""


def test_contact_add_refuses_a_link_added_twice(fake: FakeBriarClient) -> None:
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    result = briarctl(fake, "contact", "add", "briar://abc", "--alias", "Again")

    assert result.code == 1
    assert "being added already (Nat)" in result.err


def test_contact_list_shows_contacts_and_the_ones_being_added(fake: FakeBriarClient) -> None:
    fake.add_contact_record("Alice", alias="Al")
    fake.add_contact_record("Bob")
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    result = briarctl(fake, "contact", "list")

    assert result.lines == [
        "1  Al (Alice)  connected  unverified",
        "2  Bob  connected  unverified",
        "pending  Nat  waiting_for_connection",
    ]


def test_contact_list_says_when_there_are_none(fake: FakeBriarClient) -> None:
    assert briarctl(fake, "contact", "list").lines == ["No contacts."]


def test_contact_list_in_json(fake: FakeBriarClient) -> None:
    fake.add_contact_record("Alice")
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    data = briarctl(fake, "contact", "list", "--json").json

    assert data["contacts"][0]["name"] == "Alice"
    assert data["pending"][0]["alias"] == "Nat"


@pytest.mark.parametrize("reference", ["1", "Al", "Alice"])
def test_contact_remove_by_id_alias_or_name(fake: FakeBriarClient, reference: str) -> None:
    fake.add_contact_record("Alice", alias="Al")

    result = briarctl(fake, "contact", "remove", reference, "--yes")

    assert (result.code, result.lines) == (0, ["Removed Al (id 1)."])
    assert fake.contacts() == []


def test_contact_remove_asks_first(fake: FakeBriarClient) -> None:
    fake.add_contact_record("Alice")

    result = briarctl(fake, "contact", "remove", "Alice", answers=["y"])

    assert result.questions == ["Remove the contact Alice (id 1)?"]
    assert fake.contacts() == []


def test_contact_remove_does_nothing_unless_confirmed(fake: FakeBriarClient) -> None:
    fake.add_contact_record("Alice")

    result = briarctl(fake, "contact", "remove", "Alice", answers=["n"])

    assert result.code == 1
    assert "not removed" in result.err
    assert len(fake.contacts()) == 1


def test_contact_remove_stops_adding_a_pending_contact(fake: FakeBriarClient) -> None:
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    result = briarctl(fake, "contact", "remove", "Nat", answers=["y"])

    assert result.questions == ["Stop adding Nat (waiting_for_connection)?"]
    assert (result.code, result.lines) == (0, ["Stopped adding Nat."])
    assert fake.pending_contacts() == []


def test_contact_remove_stops_adding_a_pending_contact_by_its_id(fake: FakeBriarClient) -> None:
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")
    [pending] = fake.pending_contacts()

    assert briarctl(fake, "contact", "remove", pending.id, "--yes").code == 0
    assert fake.pending_contacts() == []


def test_a_pending_contact_is_kept_unless_confirmed(fake: FakeBriarClient) -> None:
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    result = briarctl(fake, "contact", "remove", "Nat")

    assert result.code == 1
    assert "not cancelled" in result.err
    assert len(fake.pending_contacts()) == 1


def test_a_real_contact_comes_before_a_pending_one_of_the_same_name(
    fake: FakeBriarClient,
) -> None:
    fake.add_contact_record("Nat")
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")

    briarctl(fake, "contact", "remove", "Nat", "--yes")

    assert fake.contacts() == []
    assert len(fake.pending_contacts()) == 1


def test_two_pending_contacts_with_one_alias_are_refused(fake: FakeBriarClient) -> None:
    briarctl(fake, "contact", "add", "briar://abc", "--alias", "Nat")
    briarctl(fake, "contact", "add", "briar://def", "--alias", "Nat")

    result = briarctl(fake, "contact", "remove", "Nat", "--yes")

    assert result.code == 1
    assert "several contacts that are being added" in result.err
    assert len(fake.pending_contacts()) == 2


def test_a_name_that_two_contacts_share_is_refused(fake: FakeBriarClient) -> None:
    fake.add_contact_record("Alice")
    fake.add_contact_record("Alice")

    result = briarctl(fake, "contact", "remove", "Alice", "--yes")

    assert result.code == 1
    assert "several contacts (1, 2): use the id" in result.err
    assert len(fake.contacts()) == 2


def test_an_unknown_contact_is_refused(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "contact", "remove", "9", "--yes")

    assert result.code == 1
    assert "no contact '9'" in result.err


# invitation list / accept / decline


def test_invitation_list_shows_group_ids(fake: FakeBriarClient) -> None:
    alice = fake.add_contact_record("Alice")
    group = fake.receive_invitation("Book club", alice)

    result = briarctl(fake, "invitation", "list")

    assert result.lines == [f"{group}  Book club  from Alice (contact 1)"]


def test_invitation_list_in_json(fake: FakeBriarClient) -> None:
    group = fake.receive_invitation("Book club", fake.add_contact_record("Alice"))

    assert briarctl(fake, "invitation", "list", "--json").json == [
        {"group_id": group, "name": "Book club", "creator": "Alice", "contact_id": 1}
    ]


def test_invitation_list_says_when_there_are_none(fake: FakeBriarClient) -> None:
    assert briarctl(fake, "invitation", "list").lines == ["No invitations."]


def test_invitation_accept_joins_the_group_and_says_what_to_do_next(
    fake: FakeBriarClient,
) -> None:
    group = fake.receive_invitation("Book club", fake.add_contact_record("Alice"))

    result = briarctl(fake, "invitation", "accept", group)

    assert result.code == 0
    assert result.lines[0] == f"Joined Book club. Put this id into chatko.yaml: {group}"
    assert "group reveal" in result.lines[1]
    assert [g.id for g in fake.groups()] == [group]
    assert fake.invitations() == []


def test_invitation_accept_takes_the_group_id_in_standard_base64_too(
    fake: FakeBriarClient,
) -> None:
    group = fake.receive_invitation("Book club", fake.add_contact_record("Alice"))
    standard = group.replace("-", "+").replace("_", "/") + "="

    assert briarctl(fake, "invitation", "accept", standard).code == 0


def test_invitation_decline_refuses_it(fake: FakeBriarClient) -> None:
    group = fake.receive_invitation("Book club", fake.add_contact_record("Alice"))

    result = briarctl(fake, "invitation", "decline", group)

    assert (result.code, result.lines) == (0, ["Declined Book club."])
    assert fake.groups() == []
    assert fake.invitations() == []


def test_there_is_no_answer_to_an_invitation_that_is_not_there(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "invitation", "accept", "A" * 43)

    assert result.code == 1
    assert "no invitation to that group" in result.err


def test_a_group_id_that_is_not_one_is_a_usage_error(
    fake: FakeBriarClient, capsys: pytest.CaptureFixture[str]
) -> None:
    result = briarctl(fake, "invitation", "accept", "nonsense")

    assert result.code == 2
    assert "is not a Briar id" in capsys.readouterr().err
    assert fake.calls == []


# group list / members


def test_group_list_shows_ids_creators_and_dissolved_groups(fake: FakeBriarClient) -> None:
    mine = fake.create_group("Family")
    theirs = fake.receive_invitation("Book club", fake.add_contact_record("Alice"))
    briarctl(fake, "invitation", "accept", theirs)
    fake.dissolve(theirs)

    result = briarctl(fake, "group", "list")

    assert result.lines == [
        f"{mine.id}  Family  created by the hub",
        f"{theirs}  Book club  created by Alice  DISSOLVED",
    ]


def test_group_list_in_json(fake: FakeBriarClient) -> None:
    mine = fake.create_group("Family")

    assert briarctl(fake, "group", "list", "--json").json == [
        {"id": mine.id, "name": "Family", "creator": "chatko", "ours": True, "dissolved": False}
    ]


def test_group_list_says_when_there_are_none(fake: FakeBriarClient) -> None:
    assert briarctl(fake, "group", "list").lines == ["No groups."]


def joined_group(fake: FakeBriarClient) -> str:
    """A group that Alice made, the hub joined, and Bob (another contact) and Carol are in."""
    alice = fake.add_contact_record("Alice")
    bob = fake.add_contact_record("Bob")
    group = fake.receive_invitation("Book club", alice)
    briarctl(fake, "invitation", "accept", group)
    fake.add_member(group, "Bob", bob)
    fake.add_member(group, "Carol")
    return group


def test_group_members_shows_contacts_and_whether_they_are_revealed(
    fake: FakeBriarClient,
) -> None:
    group = joined_group(fake)

    result = briarctl(fake, "group", "members", group)

    assert result.lines == [
        "Alice  creator  contact 1  visible",
        "chatko  member  not a contact  visible",
        "Bob  member  contact 2  invisible",
        "Carol  member  not a contact  invisible",
    ]


def test_group_members_of_our_group_shows_who_was_invited_but_has_not_joined(
    fake: FakeBriarClient,
) -> None:
    alice, bob, _carol, _dave = (
        fake.add_contact_record(n) for n in ("Alice", "Bob", "Carol", "Dave")
    )
    group = fake.create_group("Family").id
    briarctl(fake, "group", "invite", group, "Alice", "Bob", "Carol")
    fake.join(group, alice)

    result = briarctl(fake, "group", "members", group, "--json")

    assert [m["name"] for m in result.json["members"]] == ["chatko", "Alice"]
    assert result.json["invited"] == [
        {"contact_id": bob.id, "name": "Bob"},
        {"contact_id": 3, "name": "Carol"},
    ]
    text = briarctl(fake, "group", "members", group)
    assert text.lines[-2:] == [
        "Bob  invited, not joined  contact 2",
        "Carol  invited, not joined  contact 3",
    ]


def test_group_members_of_a_group_someone_else_made_does_not_ask_for_invitations(
    fake: FakeBriarClient,
) -> None:
    group = joined_group(fake)

    briarctl(fake, "group", "members", group)

    assert "sharing" not in [call[0] for call in fake.calls]


def test_a_group_is_found_among_several(fake: FakeBriarClient) -> None:
    fake.create_group("First")
    second = fake.create_group("Second").id

    assert briarctl(fake, "group", "members", second).code == 0


def test_an_unknown_group_is_refused(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "group", "members", "A" * 43)

    assert result.code == 1
    assert "the hub is in no such group" in result.err


# group reveal


def test_group_reveal_reveals_the_relationship_with_each_contact(fake: FakeBriarClient) -> None:
    group = joined_group(fake)

    result = briarctl(fake, "group", "reveal", group, "Bob")

    assert (result.code, result.lines) == (
        0,
        ["Revealed the hub's relationship with Bob in Book club."],
    )
    assert fake.members(group)[2].visibility == "revealed_by_us"


def test_group_reveal_goes_on_after_a_contact_that_is_not_a_member(fake: FakeBriarClient) -> None:
    group = joined_group(fake)
    fake.add_contact_record("Dave")

    result = briarctl(fake, "group", "reveal", group, "Dave", "Bob", "Nobody")

    assert result.code == 1
    assert result.lines == [
        "Dave: that contact has not joined the group",
        "Revealed the hub's relationship with Bob in Book club.",
        "Nobody: no contact 'Nobody' (`briarctl contact list`)",
    ]
    assert fake.members(group)[2].visibility == "revealed_by_us"


def test_group_reveal_in_json_has_a_result_for_each_contact(fake: FakeBriarClient) -> None:
    group = joined_group(fake)

    result = briarctl(fake, "group", "reveal", group, "Bob", "Nobody", "--json")

    assert result.code == 1
    assert result.json["revealed"][0] == {"contact": 2, "name": "Bob", "ok": True}
    assert result.json["revealed"][1]["ok"] is False
    assert "no contact" in result.json["revealed"][1]["error"]


def test_group_reveal_needs_a_contact(
    fake: FakeBriarClient, capsys: pytest.CaptureFixture[str]
) -> None:
    group = joined_group(fake)

    assert briarctl(fake, "group", "reveal", group).code == 2
    assert "required: contact" in capsys.readouterr().err


# group create / invite / dissolve


def test_group_create_prints_the_id_for_the_config(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "group", "create", "Family")

    [group] = fake.groups()
    assert result.code == 0
    assert result.lines[0] == f"Created Family. Put this id into chatko.yaml: {group.id}"
    assert group.ours


def test_group_create_in_json(fake: FakeBriarClient) -> None:
    result = briarctl(fake, "group", "create", "Family", "--json")

    assert result.json["id"] == fake.groups()[0].id
    assert result.json["ours"] is True


def test_group_invite_invites_each_contact_with_the_text(fake: FakeBriarClient) -> None:
    fake.add_contact_record("Alice")
    fake.add_contact_record("Bob")
    group = fake.create_group("Family").id

    result = briarctl(fake, "group", "invite", group, "1", "Bob", "--text", "Join us")

    assert (result.code, result.lines) == (
        0,
        ["Invited Alice to Family (invite_sent).", "Invited Bob to Family (invite_sent)."],
    )
    assert ("invite", group, 1, "Join us") in fake.calls


def test_group_invite_reports_a_contact_that_cannot_be_invited_and_goes_on(
    fake: FakeBriarClient,
) -> None:
    fake.add_contact_record("Alice")
    fake.add_contact_record("Bob")
    group = fake.create_group("Family").id
    briarctl(fake, "group", "invite", group, "Alice")

    result = briarctl(fake, "group", "invite", group, "Alice", "Bob")

    assert result.code == 1
    assert result.lines == [
        "Alice: that contact cannot be invited (invite_sent)",
        "Invited Bob to Family (invite_sent).",
    ]


def test_group_invite_to_a_group_the_hub_did_not_create_is_refused(
    fake: FakeBriarClient,
) -> None:
    group = joined_group(fake)

    result = briarctl(fake, "group", "invite", group, "Bob")

    assert result.code == 1
    assert "only the creator" in result.out


def test_group_dissolve_dissolves_a_group_the_hub_created(fake: FakeBriarClient) -> None:
    group = fake.create_group("Family").id

    result = briarctl(fake, "group", "dissolve", group, answers=["y"])

    assert result.questions == ["Dissolve Family? Its history is removed from the hub."]
    assert (result.code, result.lines) == (0, ["Dissolved Family."])
    assert fake.groups() == []


def test_group_dissolve_leaves_a_group_someone_else_created(fake: FakeBriarClient) -> None:
    group = joined_group(fake)

    result = briarctl(fake, "group", "dissolve", group, "--yes")

    assert result.questions == []
    assert (result.code, result.lines) == (0, ["Left Book club."])
    assert fake.groups() == []


def test_group_dissolve_does_nothing_unless_confirmed(fake: FakeBriarClient) -> None:
    group = fake.create_group("Family").id

    result = briarctl(fake, "group", "dissolve", group)

    assert result.code == 1
    assert "not done" in result.err
    assert len(fake.groups()) == 1


def test_group_dissolve_in_json(fake: FakeBriarClient) -> None:
    group = fake.create_group("Family").id

    assert briarctl(fake, "group", "dissolve", group, "--yes", "--json").json == {
        "left": group,
        "dissolved": True,
    }


# Failures of the whole call.


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (RefusedError("briar-headless rejects the auth token"), "rejects the auth token"),
        (UnreachableError("cannot reach briar-headless at http://x"), "cannot reach"),
    ],
)
def test_a_call_that_fails_prints_why_and_exits_with_1(
    fake: FakeBriarClient, error: Exception, words: str
) -> None:
    fake.fail = error  # type: ignore[assignment]

    result = briarctl(fake, "group", "list")

    assert result.code == 1
    assert result.out == ""
    assert words in result.err
    assert TOKEN not in result.err
