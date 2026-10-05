"""Briar ids in the forms `briarctl` reads and prints (design.md §7.1, §7.4)."""

import pytest

from briarctl import ids

RAW = bytes([0xFB, 0xFF, 0xFE]) + bytes(29)


def test_the_text_form_has_no_padding_and_no_slash() -> None:
    assert ids.from_bytes(RAW) == "-__-" + "A" * 39
    assert ids.from_bytes(bytes(32)) == "A" * 43


def test_the_json_form_is_standard_base64() -> None:
    assert ids.from_json("+//+" + "A" * 39 + "=") == ids.from_bytes(RAW)


@pytest.mark.parametrize(
    "text",
    [
        "-__-" + "A" * 39,
        "+//+" + "A" * 39,
        "+//+" + "A" * 39 + "=",
        "-__-" + "A" * 39 + "=",
        "  " + "-__-" + "A" * 39 + "\n",
    ],
)
def test_normalize_takes_either_form_with_or_without_padding(text: str) -> None:
    assert ids.normalize(text) == "-__-" + "A" * 39


@pytest.mark.parametrize(
    "text",
    ["", "not an id!", "A" * 42, "A" * 44, "é" * 43, "A" * 42 + "===", "A" * 40 + "=" + "AA"],
)
def test_normalize_rejects_what_is_not_an_id(text: str) -> None:
    with pytest.raises(ValueError, match="not a Briar id"):
        ids.normalize(text)


@pytest.mark.parametrize("text", ["***", "AAAA"])
def test_from_json_rejects_what_is_not_an_id(text: str) -> None:
    with pytest.raises(ValueError, match=r"base64|Briar id"):
        ids.from_json(text)
