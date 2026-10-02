"""How a message is written into Meshtastic packets (design.md §6.3): the author label and the
text, split into parts of at most 200 bytes of UTF-8, at most 3 of them, the rest cut off.

Pure functions: the extension sends what `render` returns, one packet per part.
"""

from dataclasses import dataclass
from typing import Final

MAX_PACKET_BYTES: Final = 200
"""The most text one packet carries: a direct message holds about 220 bytes and a channel
packet about 230, so this fits both (design.md §6.3)."""

MAX_PARTS: Final = 3
MAX_LABEL_BYTES: Final = 39
"""The longest label, as long as a node's long name may be; a longer one is cut."""

ELLIPSIS: Final = "…"


@dataclass(frozen=True, slots=True)
class Rendered:
    """The packets of one message, in order; `truncated` when the text did not fit whole."""

    parts: tuple[str, ...]
    truncated: bool = False


def render(label: str, text: str) -> Rendered:
    """`NatAda: text` in one packet, or `NatAda (1/3): …` parts split at whitespace."""
    label = shorten_label(label)
    whole = f"{label}: {text}"
    if _size(whole) <= MAX_PACKET_BYTES:
        return Rendered((whole,))
    room = MAX_PACKET_BYTES - _size(f"{label} ({MAX_PARTS}/{MAX_PARTS}): ")
    chunks, truncated = _split(text.strip(), room)
    count = len(chunks)
    parts = tuple(f"{label} ({n}/{count}): {chunk}" for n, chunk in enumerate(chunks, start=1))
    return Rendered(parts, truncated)


def shorten_label(label: str) -> str:
    """The label, cut to `MAX_LABEL_BYTES` with `…` if it is longer."""
    if _size(label) <= MAX_LABEL_BYTES:
        return label
    return _head(label, MAX_LABEL_BYTES - _size(ELLIPSIS)).rstrip() + ELLIPSIS


def _split(text: str, room: int) -> tuple[list[str], bool]:
    """Chunks of at most `room` bytes, at most `MAX_PARTS`; the last one ends with `…` when
    the text goes on."""
    chunks: list[str] = []
    rest = text
    while rest:
        if _size(rest) <= room:
            chunks.append(rest)
            break
        if len(chunks) == MAX_PARTS - 1:
            chunks.append(_prefix(rest, room - _size(ELLIPSIS)) + ELLIPSIS)
            return chunks, True
        chunk = _prefix(rest, room)
        chunks.append(chunk)
        rest = rest[len(chunk) :].lstrip()
    return chunks, False


def _prefix(text: str, limit: int) -> str:
    """The longest start of `text` of at most `limit` bytes that ends at whitespace, without
    the whitespace; the first `limit` bytes when a single word is longer than that. `text` is
    longer than `limit`."""
    head = _head(text, limit)
    if text[len(head)].isspace():
        return head.rstrip()
    for cut in range(len(head) - 1, 0, -1):
        if head[cut].isspace() and head[:cut].strip():
            return head[:cut].rstrip()
    return head


def _head(text: str, limit: int) -> str:
    """The first `limit` bytes of `text`, without a character cut in half."""
    return text.encode()[:limit].decode(errors="ignore")


def _size(text: str) -> int:
    return len(text.encode())
