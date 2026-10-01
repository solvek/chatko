"""Message fingerprints: the same message brought in by different paths (docs/design.md §9.5)."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from chatko.domain.errors import DomainError

_DIGEST_SIZE = 16
# Part of the hash input: a hub with another version of the algorithm never matches by accident.
_ALGORITHM = b"chatko-fp-v1"
_SEPARATOR = "\x1f"
_WHITESPACE = re.compile(r"\s+")
_HEX = re.compile(rf"[0-9a-f]{{{2 * _DIGEST_SIZE}}}")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A hash of a message's original author label and its normalized text, as 32 hex digits."""

    value: str

    def __post_init__(self) -> None:
        if not _HEX.fullmatch(self.value):
            raise DomainError(f"invalid fingerprint {self.value!r}")

    def __str__(self) -> str:
        return self.value


def fingerprint(label: str, text: str) -> Fingerprint:
    """Fingerprint a message by its original author label and its text.

    Both are normalized first, so that copies that differ only in the ways networks and peers
    change text still match: see `normalize_label` and `normalize_text`.
    """
    data = (normalize_label(label) + _SEPARATOR + normalize_text(text)).encode()
    digest = hashlib.blake2b(data, digest_size=_DIGEST_SIZE, person=_ALGORITHM)
    return Fingerprint(digest.hexdigest())


def normalize_text(text: str) -> str:
    """Unicode NFKC, case-folded, every run of whitespace made one space, the ends trimmed."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def normalize_label(label: str) -> str:
    """Unicode NFKC, case-folded, letters and digits only: `~NatAda` and `natada` match."""
    folded = unicodedata.normalize("NFKC", label).casefold()
    return "".join(char for char in folded if char.isalnum())
