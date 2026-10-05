"""`FakeBriarClient` keeps the rules of `briar-headless` that `briarctl` relies on (§7.4)."""

import pytest

from briarctl.api import NotFoundError, RejectedError
from briarctl.testing import FakeBriarClient

MISSING = "A" * 43


@pytest.fixture
def fake() -> FakeBriarClient:
    return FakeBriarClient()


def test_removing_an_unknown_contact_is_not_found(fake: FakeBriarClient) -> None:
    with pytest.raises(NotFoundError):
        fake.remove_contact(1)


def test_answering_an_unknown_invitation_is_not_found(fake: FakeBriarClient) -> None:
    with pytest.raises(NotFoundError):
        fake.answer_invitation(MISSING, accept=True)


@pytest.mark.parametrize("call", ["leave_group", "members", "sharing"])
def test_an_unknown_group_is_not_found(fake: FakeBriarClient, call: str) -> None:
    with pytest.raises(NotFoundError):
        getattr(fake, call)(MISSING)


def test_revealing_or_inviting_an_unknown_contact_is_not_found(fake: FakeBriarClient) -> None:
    group = fake.create_group("Family").id

    with pytest.raises(NotFoundError):
        fake.reveal(group, 1)
    with pytest.raises(NotFoundError):
        fake.invite(group, 1, None)


def test_only_a_member_can_be_revealed(fake: FakeBriarClient) -> None:
    contact = fake.add_contact_record("Alice")
    group = fake.create_group("Family").id

    with pytest.raises(RejectedError) as raised:
        fake.reveal(group, contact.id)

    assert raised.value.code == "NOT_MEMBER"


def test_revealing_what_is_visible_already_changes_nothing(fake: FakeBriarClient) -> None:
    alice = fake.add_contact_record("Alice")
    group = fake.create_group("Family").id
    fake.invite(group, alice.id, None)
    fake.join(group, alice)

    fake.reveal(group, alice.id)

    assert [m.visibility for m in fake.members(group)] == ["visible", "visible"]


def test_only_the_creator_lists_and_sends_invitations(fake: FakeBriarClient) -> None:
    alice = fake.add_contact_record("Alice")
    group = fake.receive_invitation("Book club", alice)
    fake.answer_invitation(group, accept=True)

    for call in (lambda: fake.sharing(group), lambda: fake.invite(group, alice.id, None)):
        with pytest.raises(RejectedError) as raised:
            call()
        assert raised.value.code == "NOT_CREATOR"


def test_a_contact_who_joined_cannot_be_invited_again(fake: FakeBriarClient) -> None:
    alice = fake.add_contact_record("Alice")
    group = fake.create_group("Family").id
    fake.join(group, alice)

    with pytest.raises(RejectedError) as raised:
        fake.invite(group, alice.id, None)

    assert (raised.value.code, "sharing" in str(raised.value)) == ("NOT_SHAREABLE", True)


def test_a_failure_set_on_the_fake_is_raised_by_every_call(fake: FakeBriarClient) -> None:
    fake.fail = NotFoundError("gone")

    with pytest.raises(NotFoundError, match="gone"):
        fake.link()


def test_cancelling_a_pending_contact_forgets_its_link(fake: FakeBriarClient) -> None:
    fake.add_contact("briar://abc", "Nat")
    [pending] = fake.pending_contacts()

    fake.cancel_pending(pending.id)

    assert fake.pending_contacts() == []
    fake.add_contact("briar://abc", "Nat")


def test_cancelling_an_unknown_pending_contact_is_not_found(fake: FakeBriarClient) -> None:
    with pytest.raises(NotFoundError):
        fake.cancel_pending(MISSING)
