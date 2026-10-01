import pytest

from chatko.domain import (
    MAX_LABEL_LENGTH,
    Account,
    AccountKey,
    Author,
    Person,
    default_label,
    generate_label,
)

TG = AccountKey("telegram", "111111111")
MESH = AccountKey("meshtastic", "!a1b2c3d4")


# The examples of docs/design.md §8.
@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("Наталія Адамчук", "NatAda"),
        ("Сергей Петров", "SerPet"),
        ("Юлія", "Yuliia"),
        ("Ada Lovelace", "AdaLov"),
        ("Jean-Luc Picard", "JeaPic"),
        ("🦊 Fox", "Fox"),
        ("Олена 🌻 Коваль", "OleKov"),
        ("Наталія Адамчук-Коваль", "NatAda"),
        ("Li Wei", "LiWei"),
        ("Я", "Ya"),
    ],
)
def test_label_from_the_name(name: str, label: str) -> None:
    assert generate_label(Account(TG, name)) == label


def test_label_from_the_short_name_when_the_name_has_no_letters() -> None:
    assert generate_label(Account(MESH, "📡 42", short_name="BC1")) == "BC1"


def test_short_name_is_transliterated() -> None:
    assert generate_label(Account(MESH, "", short_name="БК1")) == "BK1"


def test_label_from_the_account_id_without_a_usable_name() -> None:
    assert generate_label(Account(MESH, "", short_name="🦊")) == "a1b2c3"


def test_single_letter_name_is_not_enough() -> None:
    assert generate_label(Account(MESH, "Q")) == "a1b2c3"


def test_account_id_without_letters_or_digits_is_cut() -> None:
    assert generate_label(Account(AccountKey("x", "!!!!!!!!"))) == "!!!!!!"


def test_words_are_split_on_punctuation_but_not_on_apostrophes() -> None:
    assert generate_label(Account(TG, "o'brien_smith")) == "ObrSmi"


def test_digits_do_not_count_as_letters() -> None:
    assert generate_label(Account(TG, "Ada2 Lovelace")) == "AdaLov"


@pytest.mark.parametrize(
    "name",
    ["Наталія Адамчук", "Щ", "ЩЩЩЩ ЩЩЩЩ", "Wolfeschlegelsteinhausenbergerdorff", "", "🦊"],
)
def test_generated_label_has_at_most_six_characters(name: str) -> None:
    assert 1 <= len(generate_label(Account(MESH, name, "ЩЩЩЩ"))) <= MAX_LABEL_LENGTH


def test_default_label_of_a_person_is_their_label() -> None:
    person = Person("Nata", frozenset({TG}))

    assert default_label(Author(Account(TG, "Наталія Адамчук"), person)) == "Nata"


def test_default_label_of_an_unlisted_account_is_generated() -> None:
    assert default_label(Author(Account(TG, "Наталія Адамчук"))) == "NatAda"


def test_default_label_of_a_peer_relay_is_the_label_the_peer_gave() -> None:
    peer = Account(AccountKey("meshtastic", "!0badf00d"), "home hub")

    assert default_label(Author(peer, relayed_label="NatAda")) == "NatAda"
