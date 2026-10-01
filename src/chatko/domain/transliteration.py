"""Cyrillic to Latin transliteration for author labels (docs/design.md §8).

Every name is read as Ukrainian and follows the official table of the Cabinet of Ministers of
Ukraine (KMU resolution No. 55 of 2010). Letters of other Cyrillic alphabets are added in the same
spirit (ы → y, э → e, ъ dropped, ђ → dj, қ → k), and a letter with a diacritic that is in neither
table is written as its base letter (ё → e, ў → u, ќ → k).

Other characters keep their base Latin letter where Unicode gives one (é → e), a few Latin letters
without a decomposition are spelled out (ł → l), and anything else is kept as it is.
"""

import unicodedata

# KMU-2010. The second form of є ї й ю я is used everywhere except at the start of a word.
_UKRAINIAN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh",
    "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia",
}  # fmt: skip
_UKRAINIAN_WORD_START: dict[str, str] = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}

# Letters of other Cyrillic alphabets that do not decompose into a Ukrainian letter.
_OTHER_CYRILLIC: dict[str, str] = {
    # Russian
    "ы": "y", "э": "e", "ъ": "",
    # Serbian and Macedonian
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dz", "ѕ": "dz",
    # Kazakh and other Turkic alphabets
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h",
}  # fmt: skip
_CYRILLIC: dict[str, str] = _UKRAINIAN | _OTHER_CYRILLIC

# Latin letters that have no Unicode decomposition into a base letter.
_LATIN_EXTRA: dict[str, str] = {
    "ł": "l", "đ": "d", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "þ": "th", "ð": "d", "ı": "i",
}  # fmt: skip

_APOSTROPHES = frozenset("'’ʼ`")


def transliterate(text: str) -> str:
    """Write `text` in Latin letters, keeping each word's case (Щука → Shchuka, ЩУКА → SHCHUKA).

    Apostrophes inside a Cyrillic word are dropped (Мар'яна → Mariana).
    """
    out: list[str] = []
    for index, char in enumerate(text):
        previous = text[index - 1] if index > 0 else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        if char in _APOSTROPHES and _is_cyrillic(previous) and following.isalpha():
            continue
        latin = _cyrillic(char.lower(), previous)
        if latin is None:
            out.append(_latin(char))
        else:
            out.append(_with_case(latin, char, previous, following))
    return "".join(out)


def _cyrillic(lower: str, previous: str) -> str | None:
    if lower not in _CYRILLIC:
        lower = unicodedata.normalize("NFKD", lower)[0]  # ё → е, ў → у
    if lower in _UKRAINIAN_WORD_START and _starts_word(previous):
        return _UKRAINIAN_WORD_START[lower]
    if lower == "г" and previous.lower() == "з":
        return "gh"  # KMU-2010 writes зг as zgh, to tell it from ж (zh)
    return _CYRILLIC.get(lower)


def _starts_word(previous: str) -> bool:
    return not previous.isalpha() and previous not in _APOSTROPHES


def _with_case(latin: str, char: str, previous: str, following: str) -> str:
    if not char.isupper():
        return latin
    if following.isupper() or (not following.isalpha() and previous.isupper()):
        return latin.upper()
    return latin.capitalize()


def _latin(char: str) -> str:
    lower = char.lower()
    if lower in _LATIN_EXTRA:
        latin = _LATIN_EXTRA[lower]
        return latin.capitalize() if char.isupper() else latin
    base = "".join(c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c))
    return base if base.isascii() and base.isalpha() else char


def _is_cyrillic(char: str) -> bool:
    return bool(char) and "CYRILLIC" in unicodedata.name(char, "")
