"""Briar ids: 32 bytes, written in two forms (design.md §7.1, §7.4).

In the API's JSON, an id is standard base64. In a URL path, in the config and in logs it is
URL-safe base64 without padding (43 characters), because a standard one can contain `/`.
"""

import base64
import binascii

ID_SIZE = 32


def from_json(text: str) -> bytes:
    """The id from the standard base64 of the API's JSON."""
    try:
        value = base64.b64decode(text, validate=True)
    except binascii.Error:
        raise ValueError(f"{text!r} is not base64") from None
    return _checked(value)


def from_text(text: str) -> bytes:
    """The id from its URL-safe form, as `briarctl` prints it. Padding is optional."""
    try:
        value = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise ValueError(f"{text!r} is not a Briar id (URL-safe base64)") from None
    if "=" in text or not text.isascii():
        raise ValueError(f"{text!r} is not a Briar id (URL-safe base64, no padding)")
    return _checked(value)


def to_json(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def to_text(value: bytes) -> str:
    """The URL-safe form without padding, for paths, the config, logs and transport ids."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _checked(value: bytes) -> bytes:
    if len(value) != ID_SIZE:
        raise ValueError(f"a Briar id is {ID_SIZE} bytes, not {len(value)}")
    return value
