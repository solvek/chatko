"""Author labels: the short name in front of a relayed message (docs/design.md §8)."""

import re

from chatko.domain.accounts import Account, Author
from chatko.domain.transliteration import transliterate

MAX_LABEL_LENGTH = 6
_PART_LENGTH = 3
_MIN_NAME_LETTERS = 2

# A word is a run of letters and digits, with apostrophes and hyphens inside it (Jean-Luc, O'Brien).
_WORD = re.compile(r"(?:[^\W_]|['’ʼ`‐-])+")  # noqa: RUF001 (the apostrophes are intended)


def default_label(author: Author) -> str:
    """The label the core uses unless the routing script's `label` hook gives another one.

    A peer's relay keeps the label the peer gave; a person from the config is signed with their
    label; anyone else gets a label generated from the account.
    """
    if author.relayed_label is not None:
        return author.relayed_label
    if author.person is not None:
        return author.person.label
    return generate_label(author.account)


def generate_label(account: Account) -> str:
    """A short Latin label for an account that the config does not list. Not unique.

    From the display name: the first 3 letters of the first two words, each capitalized
    (Наталія Адамчук → NatAda), or the first 6 letters of a single word. If the name gives fewer
    than 2 Latin letters: the network's short name, else the start of the account id, both cut to 6
    letters and digits.
    """
    return (
        _from_name(account.display_name)
        or _compact(account.short_name or "")
        or _compact(account.key.external_id)
        or account.key.external_id[:MAX_LABEL_LENGTH]
    )


def _from_name(name: str) -> str:
    words = [letters for word in _WORD.findall(transliterate(name)) if (letters := _letters(word))]
    if not words:
        return ""
    if len(words) == 1:
        label = words[0][:MAX_LABEL_LENGTH].capitalize()
    else:
        label = words[0][:_PART_LENGTH].capitalize() + words[1][:_PART_LENGTH].capitalize()
    return label if len(label) >= _MIN_NAME_LETTERS else ""


def _letters(word: str) -> str:
    return "".join(char for char in word if char.isascii() and char.isalpha())


def _compact(text: str) -> str:
    latin = transliterate(text)
    return "".join(char for char in latin if char.isascii() and char.isalnum())[:MAX_LABEL_LENGTH]
