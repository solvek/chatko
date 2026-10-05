"""The HTTP client over a scripted `briar-headless`: what it asks and how it reads the answers."""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from briarctl import ids
from briarctl.api import (
    BriarError,
    Contact,
    Group,
    Invitation,
    Member,
    NotFoundError,
    PendingContact,
    RefusedError,
    RejectedError,
    Sharing,
    UnreachableError,
)
from briarctl.http_client import HttpBriarClient

TOKEN = "s3cret-token"
GROUP = bytes(range(32))
SLASHY_GROUP = bytes([0xFB, 0xFF, 0xFE]) + bytes(29)
AUTHOR = bytes(range(1, 33))

Handler = Callable[[httpx.Request], httpx.Response]


def author(name: str = "Alice") -> dict[str, Any]:
    return {"formatVersion": 1, "id": _json(AUTHOR), "name": name, "publicKey": "AAAA"}


def _json(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def client_with(handler: Handler) -> HttpBriarClient:
    return HttpBriarClient(
        "http://briar:7000/",
        TOKEN,
        client=httpx.Client(
            base_url="http://briar:7000",
            headers={"Authorization": f"Bearer {TOKEN}"},
            transport=httpx.MockTransport(handler),
        ),
    )


def answering(body: Any, status: int = 200) -> tuple[HttpBriarClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return client_with(handler), seen


def test_every_request_carries_the_token_in_the_authorization_header() -> None:
    api, seen = answering([])

    api.contacts()

    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_it_reads_the_hubs_link() -> None:
    api, seen = answering({"link": "briar://abc"})

    assert api.link() == "briar://abc"
    assert (seen[0].method, seen[0].url.path) == ("GET", "/v1/contacts/add/link")


def test_it_reads_contacts_with_and_without_an_alias() -> None:
    body = [
        {
            "author": author("Alice"),
            "contactId": 1,
            "alias": "Al",
            "verified": True,
            "connected": True,
        },
        {"author": author("Bob"), "contactId": 2},
    ]
    api, _ = answering(body)

    assert api.contacts() == [
        Contact(1, "Alice", "Al", ids.from_bytes(AUTHOR), connected=True, verified=True),
        Contact(2, "Bob", None, ids.from_bytes(AUTHOR), connected=False, verified=False),
    ]


def test_it_reads_pending_contacts() -> None:
    body = [
        {
            "pendingContact": {"pendingContactId": "abc=", "alias": "Nat", "timestamp": 1},
            "state": "waiting_for_connection",
        }
    ]
    api, seen = answering(body)

    assert api.pending_contacts() == [PendingContact("abc=", "Nat", "waiting_for_connection")]
    assert seen[0].url.path == "/v1/contacts/add/pending"


def test_adding_a_contact_posts_the_link_and_the_alias() -> None:
    api, seen = answering({"pendingContactId": "x", "alias": "Nat", "timestamp": 1})

    api.add_contact("briar://abc", "Nat")

    assert (seen[0].method, seen[0].url.path) == ("POST", "/v1/contacts/add/pending")
    assert json.loads(seen[0].content) == {"link": "briar://abc", "alias": "Nat"}


def test_cancelling_a_pending_contact_deletes_it_with_its_id_in_the_body() -> None:
    api, seen = answering(None)

    api.cancel_pending("abc=")

    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/v1/contacts/add/pending")
    assert json.loads(seen[0].content) == {"pendingContactId": "abc="}


def test_removing_a_contact_deletes_it_by_id() -> None:
    api, seen = answering(None)

    api.remove_contact(7)

    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/v1/contacts/7")


def group_json(
    raw: bytes = GROUP, *, ours: bool = False, dissolved: bool = False
) -> dict[str, Any]:
    return {
        "creator": author("Alice"),
        "dissolved": dissolved,
        "id": _json(raw),
        "name": "Family",
        "ourGroup": ours,
    }


def test_it_reads_groups_with_ids_in_the_url_safe_form() -> None:
    api, _ = answering([group_json(SLASHY_GROUP, ours=True)])

    [group] = api.groups()

    assert group == Group(
        ids.from_bytes(SLASHY_GROUP), "Family", "Alice", ours=True, dissolved=False
    )
    assert "/" not in group.id


def test_creating_a_group_posts_its_name() -> None:
    api, seen = answering(group_json(ours=True))

    group = api.create_group("Family")

    assert group.ours
    assert (seen[0].method, seen[0].url.path) == ("POST", "/v1/groups")
    assert json.loads(seen[0].content) == {"name": "Family"}


def test_a_group_id_goes_into_the_path_as_it_is() -> None:
    api, seen = answering(None)
    group = ids.from_bytes(SLASHY_GROUP)

    api.leave_group(group)

    assert (seen[0].method, seen[0].url.path) == ("DELETE", f"/v1/groups/{group}")


def test_it_reads_members_with_and_without_a_contact() -> None:
    body = [
        {"author": author("Alice"), "authorStatus": "unverified", "contactId": 1, "creator": True,
         "visibility": "visible"},
        {"author": author("Bob"), "authorStatus": "unknown", "contactId": None, "creator": False,
         "visibility": "invisible"},
    ]  # fmt: skip
    api, seen = answering(body)

    members = api.members(ids.from_bytes(GROUP))

    assert members == [
        Member(ids.from_bytes(AUTHOR), "Alice", True, 1, "visible"),
        Member(ids.from_bytes(AUTHOR), "Bob", False, None, "invisible"),
    ]
    assert seen[0].url.path == f"/v1/groups/{ids.from_bytes(GROUP)}/members"


def test_revealing_posts_the_contact_id() -> None:
    api, seen = answering(None)

    api.reveal(ids.from_bytes(GROUP), 3)

    assert (seen[0].method, seen[0].url.path) == (
        "POST",
        f"/v1/groups/{ids.from_bytes(GROUP)}/members/reveal",
    )
    assert json.loads(seen[0].content) == {"contactId": 3}


def test_it_reads_who_can_be_invited() -> None:
    api, seen = answering(
        [{"contactId": 1, "status": "shareable"}, {"contactId": 2, "status": "sharing"}]
    )

    assert api.sharing(ids.from_bytes(GROUP)) == [Sharing(1, "shareable"), Sharing(2, "sharing")]
    assert seen[0].url.path == f"/v1/groups/{ids.from_bytes(GROUP)}/invitations"


@pytest.mark.parametrize(("text", "body"), [("Join us", {"contactId": 1, "text": "Join us"}),
                                            (None, {"contactId": 1})])  # fmt: skip
def test_inviting_posts_the_contact_and_the_text_if_there_is_one(
    text: str | None, body: dict[str, Any]
) -> None:
    api, seen = answering({"contactId": 1, "status": "invite_sent"})

    assert api.invite(ids.from_bytes(GROUP), 1, text) == Sharing(1, "invite_sent")
    assert json.loads(seen[0].content) == body


def test_it_reads_the_invitations_to_the_hub() -> None:
    body = [
        {"contactId": 4, "creator": author("Alice"), "groupId": _json(GROUP), "name": "Book club"}
    ]
    api, seen = answering(body)

    assert api.invitations() == [Invitation(ids.from_bytes(GROUP), "Book club", "Alice", 4)]
    assert seen[0].url.path == "/v1/groups/invitations"


@pytest.mark.parametrize("accept", [True, False])
def test_answering_an_invitation_posts_the_decision(*, accept: bool) -> None:
    api, seen = answering(None)

    api.answer_invitation(ids.from_bytes(GROUP), accept=accept)

    assert seen[0].url.path == f"/v1/groups/invitations/{ids.from_bytes(GROUP)}"
    assert json.loads(seen[0].content) == {"accept": accept}


# Errors.


def failing(
    status: int,
    body: Any = None,
) -> HttpBriarClient:
    return answering(body, status)[0]


def test_a_refused_token_is_refused_and_never_shown() -> None:
    with pytest.raises(RefusedError) as raised:
        failing(401).contacts()

    assert TOKEN not in str(raised.value)


def test_an_unknown_group_is_not_found() -> None:
    with pytest.raises(NotFoundError, match="no such"):
        failing(404).members(ids.from_bytes(GROUP))


@pytest.mark.parametrize(
    ("body", "code", "words"),
    [
        ({"error": "NOT_CREATOR"}, "NOT_CREATOR", "creator"),
        ({"error": "NOT_SHAREABLE", "status": "invite_sent"}, "NOT_SHAREABLE", "invite_sent"),
        ({"error": "NOT_MEMBER"}, "NOT_MEMBER", "not joined"),
        ({"error": "INVALID_LINK"}, "INVALID_LINK", "valid briar://"),
        ({"error": "CONTACT_EXISTS", "remoteAuthorName": "Bob"}, "CONTACT_EXISTS", "Bob"),
        ({"error": "PENDING_EXISTS", "pendingContactAlias": "Ann"}, "PENDING_EXISTS", "Ann"),
        ({"error": "SOMETHING_NEW"}, "SOMETHING_NEW", "SOMETHING_NEW"),
        ({}, None, "no reason"),
        ("not an object", None, "no reason"),
    ],
)
def test_a_refusal_says_why_in_words(body: Any, code: str | None, words: str) -> None:
    with pytest.raises(RejectedError, match=words) as raised:
        failing(403, body).invite(ids.from_bytes(GROUP), 1, None)

    assert raised.value.code == code


def test_a_refusal_without_json_is_a_refusal_without_a_reason() -> None:
    api = client_with(lambda _request: httpx.Response(400, content=b"<html>"))

    with pytest.raises(RejectedError, match="no reason"):
        api.create_group("")


def test_a_redirect_is_a_failure_not_a_success() -> None:
    with pytest.raises(RejectedError, match="no reason"):
        failing(302).leave_group(ids.from_bytes(GROUP))


@pytest.mark.parametrize("status", [500, 503, 429])
def test_a_failing_server_is_unreachable(status: int) -> None:
    with pytest.raises(UnreachableError, match=str(status)):
        failing(status).groups()


def test_a_connection_that_fails_is_unreachable_and_shows_the_address_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(UnreachableError, match=r"http://briar:7000 \(ConnectError\)") as raised:
        client_with(handler).groups()

    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [{"not": "a list"}, ["not an object"], [{"id": "AAAA"}], [{"contactId": "x", "author": {}}]],
)
def test_an_answer_in_a_form_it_does_not_know_is_an_error(body: Any) -> None:
    with pytest.raises(BriarError, match="cannot read"):
        answering(body)[0].contacts()


def test_an_answer_that_is_not_json_is_an_error() -> None:
    api = client_with(lambda _request: httpx.Response(200, content=b"<html>"))

    with pytest.raises(BriarError, match="cannot read"):
        api.link()


def test_close_closes_the_connection_pool() -> None:
    api = HttpBriarClient("http://briar:7000", TOKEN)

    api.close()

    with pytest.raises(RuntimeError, match="closed"):
        api.contacts()
