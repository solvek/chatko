"""`HttpBriarClient`: the `BriarClient` port over `httpx`, synchronously (design.md §7.4, §7.5).

The token travels only in the `Authorization` header, and no error text shows it.
"""

from collections.abc import Callable
from typing import Any

import httpx

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
    Sharing,
    UnreachableError,
    rejection,
)

REQUEST_TIMEOUT = 30.0
"""Seconds that one request may take."""


class HttpBriarClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = REQUEST_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._client = client or httpx.Client(
            base_url=self._url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def link(self) -> str:
        return self._one("GET", "/v1/contacts/add/link", lambda item: str(item["link"]))

    def contacts(self) -> list[Contact]:
        return self._many("GET", "/v1/contacts", _contact)

    def pending_contacts(self) -> list[PendingContact]:
        return self._many("GET", "/v1/contacts/add/pending", _pending)

    def add_contact(self, link: str, alias: str) -> None:
        body = {"link": link, "alias": alias}
        self._call("POST", "/v1/contacts/add/pending", body)

    def remove_contact(self, contact_id: int) -> None:
        self._call("DELETE", f"/v1/contacts/{contact_id}")

    def cancel_pending(self, pending_id: str) -> None:
        self._call("DELETE", "/v1/contacts/add/pending", {"pendingContactId": pending_id})

    def invitations(self) -> list[Invitation]:
        return self._many("GET", "/v1/groups/invitations", _invitation)

    def answer_invitation(self, group_id: str, *, accept: bool) -> None:
        self._call("POST", f"/v1/groups/invitations/{group_id}", {"accept": accept})

    def groups(self) -> list[Group]:
        return self._many("GET", "/v1/groups", _group)

    def create_group(self, name: str) -> Group:
        return self._one("POST", "/v1/groups", _group, {"name": name})

    def leave_group(self, group_id: str) -> None:
        self._call("DELETE", f"/v1/groups/{group_id}")

    def members(self, group_id: str) -> list[Member]:
        return self._many("GET", f"/v1/groups/{group_id}/members", _member)

    def reveal(self, group_id: str, contact_id: int) -> None:
        self._call("POST", f"/v1/groups/{group_id}/members/reveal", {"contactId": contact_id})

    def sharing(self, group_id: str) -> list[Sharing]:
        return self._many("GET", f"/v1/groups/{group_id}/invitations", _sharing)

    def invite(self, group_id: str, contact_id: int, text: str | None) -> Sharing:
        body: dict[str, Any] = {"contactId": contact_id}
        if text:
            body["text"] = text
        return self._one("POST", f"/v1/groups/{group_id}/invitations", _sharing, body)

    def _many[T](self, method: str, path: str, parse: Callable[[Any], T]) -> list[T]:
        answer = self._call(method, path)
        if not isinstance(answer, list):
            raise _unexpected(method, path)
        return [self._decode(method, path, parse, item) for item in answer]

    def _one[T](
        self, method: str, path: str, parse: Callable[[Any], T], body: dict[str, Any] | None = None
    ) -> T:
        return self._decode(method, path, parse, self._call(method, path, body))

    @staticmethod
    def _decode[T](method: str, path: str, parse: Callable[[Any], T], item: object) -> T:
        try:
            return parse(item)
        except (KeyError, TypeError, ValueError):
            raise _unexpected(method, path) from None

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - the JSON the API sends
        try:
            response = self._client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise UnreachableError(
                f"cannot reach briar-headless at {self._url} ({type(error).__name__})"
            ) from None
        status = response.status_code
        if status >= 300:
            raise _failure(method, path, status, response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise _unexpected(method, path) from None


def _failure(method: str, path: str, status: int, response: httpx.Response) -> BriarError:
    if status == 401:
        return RefusedError("briar-headless rejects the auth token")
    if status == 404:
        return NotFoundError("briar-headless has no such group, contact or invitation")
    if status >= 500 or status == 429:
        return UnreachableError(f"briar-headless failed: {method} {path} answered {status}")
    answer = _object(response)
    detail = (
        answer.get("status") or answer.get("remoteAuthorName") or answer.get("pendingContactAlias")
    )
    return rejection(_text(answer.get("error")), _text(detail))


def _object(response: httpx.Response) -> dict[str, Any]:
    try:
        answer = response.json()
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _unexpected(method: str, path: str) -> BriarError:
    return UnreachableError(
        f"briar-headless sent an answer to {method} {path} that briarctl cannot read"
    )


def _contact(item: dict[str, Any]) -> Contact:
    return Contact(
        id=int(item["contactId"]),
        name=str(item["author"]["name"]),
        alias=item.get("alias"),
        author_id=ids.from_json(item["author"]["id"]),
        connected=bool(item.get("connected", False)),
        verified=bool(item.get("verified", False)),
    )


def _pending(item: dict[str, Any]) -> PendingContact:
    return PendingContact(
        id=str(item["pendingContact"]["pendingContactId"]),
        alias=str(item["pendingContact"]["alias"]),
        state=str(item["state"]),
    )


def _group(item: dict[str, Any]) -> Group:
    return Group(
        id=ids.from_json(item["id"]),
        name=str(item["name"]),
        creator=str(item["creator"]["name"]),
        ours=bool(item["ourGroup"]),
        dissolved=bool(item["dissolved"]),
    )


def _member(item: dict[str, Any]) -> Member:
    return Member(
        author_id=ids.from_json(item["author"]["id"]),
        name=str(item["author"]["name"]),
        creator=bool(item["creator"]),
        contact_id=None if item["contactId"] is None else int(item["contactId"]),
        visibility=str(item["visibility"]),
    )


def _sharing(item: dict[str, Any]) -> Sharing:
    return Sharing(contact_id=int(item["contactId"]), status=str(item["status"]))


def _invitation(item: dict[str, Any]) -> Invitation:
    return Invitation(
        group_id=ids.from_json(item["groupId"]),
        name=str(item["name"]),
        creator=str(item["creator"]["name"]),
        contact_id=int(item["contactId"]),
    )
