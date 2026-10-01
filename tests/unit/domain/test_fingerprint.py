import pytest

from chatko.domain import DomainError, Fingerprint, fingerprint, normalize_label, normalize_text


def test_fingerprint_is_32_hex_digits() -> None:
    value = fingerprint("NatAda", "Привіт").value

    assert len(value) == 32
    assert int(value, 16) >= 0


def test_fingerprint_is_stable() -> None:
    # Hubs compare fingerprints with each other, so the algorithm must not change silently.
    assert fingerprint("NatAda", "Привіт").value == "3f5e4c402a0d578c4f292f01c5f7bebc"


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("NatAda", "Привіт,  світ"),
        ("natada", "привіт, світ"),
        ("~NatAda", " Привіт,\nсвіт "),
        ("Nat Ada", "ПРИВІТ, СВІТ"),
    ],
)
def test_copies_that_differ_in_case_spacing_or_marks_match(label: str, text: str) -> None:
    assert fingerprint(label, text) == fingerprint("NatAda", "Привіт, світ")


def test_different_authors_differ() -> None:
    assert fingerprint("NatAda", "ok") != fingerprint("SerPet", "ok")


def test_different_texts_differ() -> None:
    assert fingerprint("NatAda", "ok") != fingerprint("NatAda", "ok!")


def test_label_and_text_cannot_run_into_each_other() -> None:
    assert fingerprint("Nat", "ada ok") != fingerprint("NatAda", "ok")


def test_normalize_text_uses_nfkc() -> None:
    assert normalize_text("ﬁle  №1") == "file no1"


def test_normalize_label_keeps_letters_and_digits() -> None:
    assert normalize_label("~Nat-Ada 2") == "natada2"


def test_fingerprint_is_written_as_its_value() -> None:
    assert str(Fingerprint("0" * 32)) == "0" * 32


@pytest.mark.parametrize("value", ["", "0" * 31, "0" * 33, "G" * 32, "A" * 32])
def test_fingerprint_rejects_other_values(value: str) -> None:
    with pytest.raises(DomainError):
        Fingerprint(value)
