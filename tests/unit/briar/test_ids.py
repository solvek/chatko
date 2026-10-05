"""Briar ids in their two forms (design.md §7.1)."""

import pytest

from chatko_briar import ids

RAW = bytes(range(32))
JSON_FORM = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
TEXT_FORM = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


def test_the_json_form_is_standard_base64() -> None:
    assert ids.to_json(RAW) == JSON_FORM
    assert ids.from_json(JSON_FORM) == RAW


def test_the_text_form_is_url_safe_without_padding() -> None:
    assert ids.to_text(RAW) == TEXT_FORM
    assert ids.from_text(TEXT_FORM) == RAW


def test_a_slash_in_the_json_form_is_a_underscore_in_the_text_form() -> None:
    raw = bytes([0xFB, 0xFF, 0xFE]) + bytes(29)

    assert "/" in ids.to_json(raw)
    assert "/" not in ids.to_text(raw)
    assert ids.from_text(ids.to_text(raw)) == raw


@pytest.mark.parametrize("text", ["", "not base64!", TEXT_FORM[:-4], TEXT_FORM + "=", "é" * 43])
def test_from_text_rejects_what_is_not_an_id(text: str) -> None:
    with pytest.raises(ValueError, match="Briar id"):
        ids.from_text(text)


@pytest.mark.parametrize("text", ["", "***", JSON_FORM[:-4]])
def test_from_json_rejects_what_is_not_an_id(text: str) -> None:
    with pytest.raises(ValueError, match=r"base64|bytes"):
        ids.from_json(text)
