"""How domain values are stored: times as UTC text that sorts, authors as JSON."""

import json
from datetime import UTC, datetime
from typing import Any

from chatko.domain import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Author,
    EndpointRef,
    Person,
)

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def encode_time(time: datetime) -> str:
    return time.astimezone(UTC).strftime(_TIME_FORMAT)


def decode_time(text: str) -> datetime:
    return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=UTC)


def encode_author(author: Author) -> str:
    account = author.account
    person = author.person
    return json.dumps(
        {
            "key": str(account.key),
            "display_name": account.display_name,
            "short_name": account.short_name,
            "person": None
            if person is None
            else {"label": person.label, "accounts": sorted(str(a) for a in person.accounts)},
            "relayed_label": author.relayed_label,
        }
    )


def decode_author(text: str) -> Author:
    data: dict[str, Any] = json.loads(text)
    account = Account(AccountKey.parse(data["key"]), data["display_name"], data["short_name"])
    person = data["person"]
    return Author(
        account,
        None
        if person is None
        else Person(person["label"], frozenset(AccountKey.parse(a) for a in person["accounts"])),
        data["relayed_label"],
    )


def encode_attachments(attachments: tuple[Attachment, ...]) -> str:
    return json.dumps([a.kind.value for a in attachments])


def decode_attachments(text: str) -> tuple[Attachment, ...]:
    return tuple(Attachment(AttachmentKind(kind)) for kind in json.loads(text))


def endpoint_columns(endpoint: EndpointRef | None) -> tuple[str, str]:
    """`(instance, name)`; an account heard anywhere has no endpoint and is stored as `('', '')`."""
    return ("", "") if endpoint is None else (endpoint.instance, endpoint.name)
