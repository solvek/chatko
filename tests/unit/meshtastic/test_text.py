"""How a message is written into Meshtastic packets: the 200-byte limit, parts, truncation."""

import re

from hypothesis import given
from hypothesis import strategies as st

from chatko_meshtastic.text import (
    MAX_LABEL_BYTES,
    MAX_PACKET_BYTES,
    MAX_PARTS,
    Rendered,
    render,
    shorten_label,
)

CYRILLIC_WORD = "Привіт"  # 12 bytes


def size(text: str) -> int:
    return len(text.encode())


def test_a_short_message_is_one_packet_without_part_numbers() -> None:
    assert render("NatAda", "Привіт усім") == Rendered(("NatAda: Привіт усім",))


def test_a_message_of_exactly_one_packet_is_not_split() -> None:
    text = "a" * (MAX_PACKET_BYTES - size("NatAda: "))

    assert render("NatAda", text) == Rendered((f"NatAda: {text}",))


def test_a_longer_message_is_split_at_spaces_into_numbered_parts() -> None:
    words = [f"{CYRILLIC_WORD}{n}" for n in range(20)]  # 13 bytes and a space each

    rendered = render("NatAda", " ".join(words))

    assert not rendered.truncated
    assert [part.split(":")[0] for part in rendered.parts] == ["NatAda (1/2)", "NatAda (2/2)"]
    assert all(size(part) <= MAX_PACKET_BYTES for part in rendered.parts)
    assert " ".join(part.split(": ", 1)[1] for part in rendered.parts).split() == words


def test_fills_each_part_before_the_next() -> None:
    words = ["x" * 9] * 40  # 10 bytes with the space

    first = render("Ada", " ".join(words)).parts[0]

    assert size(first) > MAX_PACKET_BYTES - 10


def test_three_parts_at_most_and_the_rest_cut_with_an_ellipsis() -> None:
    text = " ".join(f"word{n}" for n in range(200))

    rendered = render("NatAda", text)

    assert rendered.truncated
    assert len(rendered.parts) == MAX_PARTS
    assert rendered.parts[2].startswith("NatAda (3/3): ")
    assert rendered.parts[2].endswith("…")
    assert not rendered.parts[2].endswith(" …")
    assert size(rendered.parts[2]) <= MAX_PACKET_BYTES


def test_a_word_longer_than_a_part_is_cut_between_characters() -> None:
    text = "я" * 150  # 300 bytes, no space

    rendered = render("Ada", text)

    assert len(rendered.parts) == 2
    assert "".join(part.split(": ", 1)[1] for part in rendered.parts) == text


def test_never_cuts_a_character_in_half() -> None:
    text = "😀" * 160

    rendered = render("Ada", text)

    assert rendered.truncated
    assert all(set(part.split(": ", 1)[1]) <= {"😀", "…"} for part in rendered.parts)


def test_keeps_line_breaks_inside_a_part() -> None:
    text = "перший рядок\nдругий рядок " + "слово " * 40

    rendered = render("Ada", text)

    assert rendered.parts[0].startswith("Ada (1/3): перший рядок\nдругий рядок слово")


def test_a_part_does_not_start_or_end_with_whitespace() -> None:
    text = ("слово   " * 40).strip()

    for part in render("Ada", text).parts:
        body = part.split(": ", 1)[1]
        assert body == body.strip()


def test_a_long_label_is_cut() -> None:
    label = "Наталія Адамчук-Коваль з Києва"  # 55 bytes

    short = shorten_label(label)

    assert short.endswith("…")
    assert size(short) <= MAX_LABEL_BYTES
    assert label.startswith(short.removesuffix("…"))
    assert render(label, "так").parts == (f"{short}: так",)


def test_a_label_of_the_longest_length_is_kept() -> None:
    label = "a" * MAX_LABEL_BYTES

    assert shorten_label(label) == label


@given(
    label=st.text(min_size=1, max_size=60).filter(str.strip),
    text=st.text(min_size=1, max_size=1200).filter(str.strip),
)
def test_every_part_fits_a_packet(label: str, text: str) -> None:
    rendered = render(label, text)

    assert 1 <= len(rendered.parts) <= MAX_PARTS
    assert all(size(part) <= MAX_PACKET_BYTES for part in rendered.parts)
    if len(rendered.parts) > 1:
        count = len(rendered.parts)
        for number, part in enumerate(rendered.parts, start=1):
            assert part.startswith(f"{shorten_label(label)} ({number}/{count}): ")


@given(
    words=st.lists(
        st.text(alphabet=st.characters(codec="utf-8", categories=["L", "N", "So"]), min_size=1),
        min_size=1,
        max_size=60,
    )
)
def test_a_text_that_is_not_cut_loses_nothing_but_whitespace(words: list[str]) -> None:
    text = " ".join(words)

    rendered = render("Ada", text)

    if not rendered.truncated:
        bodies = [re.sub(r"^Ada( \(\d/\d\))?: ", "", part) for part in rendered.parts]
        assert "".join(bodies).replace(" ", "") == text.replace(" ", "")
