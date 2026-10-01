import pytest

from chatko.domain import Account, AccountKey, Author, DomainError, Person

NAT_TG = AccountKey("telegram", "111111111")
NAT_MESH = AccountKey("meshtastic", "!a1b2c3d4")


def test_account_key_is_written_as_kind_and_id() -> None:
    assert str(NAT_MESH) == "meshtastic:!a1b2c3d4"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("telegram:111111111", NAT_TG),
        ("meshtastic:!a1b2c3d4", NAT_MESH),
        ("briar:a-b_c", AccountKey("briar", "a-b_c")),
        ("x:a:b", AccountKey("x", "a:b")),
    ],
)
def test_account_key_parses_the_config_form(text: str, key: AccountKey) -> None:
    assert AccountKey.parse(text) == key


@pytest.mark.parametrize("text", ["telegram", "", ":123", "telegram:", "Telegram:1", "tg x:1"])
def test_account_key_rejects_malformed_text(text: str) -> None:
    with pytest.raises(DomainError):
        AccountKey.parse(text)


def test_account_id_cannot_have_surrounding_whitespace() -> None:
    with pytest.raises(DomainError):
        AccountKey("telegram", " 1")


def test_account_names_are_optional() -> None:
    account = Account(NAT_MESH)

    assert account.display_name == ""
    assert account.short_name is None


def test_person_has_a_label_and_accounts() -> None:
    person = Person("NatAda", frozenset({NAT_TG, NAT_MESH}))

    assert NAT_MESH in person.accounts


@pytest.mark.parametrize("label", ["", "  ", " NatAda", "NatAda ", "Nat\nAda"])
def test_person_label_must_be_a_clean_single_line(label: str) -> None:
    with pytest.raises(DomainError):
        Person(label, frozenset({NAT_TG}))


def test_person_needs_an_account() -> None:
    with pytest.raises(DomainError, match="no accounts"):
        Person("NatAda", frozenset())


def test_author_may_be_a_listed_person() -> None:
    person = Person("NatAda", frozenset({NAT_TG}))

    assert Author(Account(NAT_TG, "Наталія"), person).person == person


def test_author_cannot_be_a_person_who_does_not_have_the_account() -> None:
    person = Person("NatAda", frozenset({NAT_TG}))

    with pytest.raises(DomainError, match="not an account"):
        Author(Account(NAT_MESH), person)


def test_relayed_author_needs_a_label() -> None:
    with pytest.raises(DomainError, match="label"):
        Author(Account(NAT_MESH), relayed_label=" ")
