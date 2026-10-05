"""Briar ids: 32 bytes, written in URL-safe base64 without padding (design.md §7.1, §7.4).

The API's JSON uses standard base64; a path, `chatko.yaml` and the tool's output use the URL-safe
form, 43 characters of `A-Z a-z 0-9 - _`. The tool accepts either form from the admin and prints
the URL-safe one.
"""

import base64
import binascii

ID_SIZE = 32
_TEXT_SIZE = 43
"""Characters of an id in URL-safe base64 without padding."""


def normalize(text: str) -> str:
    """The URL-safe form of an id given in either base64 form (`-_` or `+/`), padded or not."""
    value = text.strip()
    if len(value) not in {_TEXT_SIZE, _TEXT_SIZE + 1} or "=" in value[:_TEXT_SIZE]:
        raise ValueError(f"{text!r} is not a Briar id")
    try:
        raw = base64.b64decode(value.ljust(_TEXT_SIZE + 1, "="), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{text!r} is not a Briar id") from None
    return from_bytes(_checked(raw, text))


def from_json(text: str) -> str:
    """The URL-safe form of an id from the API's JSON (standard base64)."""
    try:
        raw = base64.b64decode(text, validate=True)
    except binascii.Error:
        raise ValueError(f"{text!r} is not base64") from None
    return from_bytes(_checked(raw, text))


def from_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _checked(raw: bytes, text: str) -> bytes:
    if len(raw) != ID_SIZE:
        raise ValueError(f"{text!r} is not a Briar id: it has {len(raw)} bytes, not {ID_SIZE}")
    return raw
