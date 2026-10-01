import pytest

from chatko.domain import transliterate


@pytest.mark.parametrize(
    ("ukrainian", "latin"),
    [
        # Examples from the KMU-2010 table itself.
        ("Алушта", "Alushta"),
        ("Борщагівка", "Borshchahivka"),
        ("Вишгород", "Vyshhorod"),
        ("Гадяч", "Hadiach"),
        ("Згорани", "Zghorany"),
        ("Ґалаґан", "Galagan"),
        ("Дон", "Don"),
        ("Рівне", "Rivne"),
        ("Єнакієве", "Yenakiieve"),
        ("Наєнко", "Naienko"),
        ("Житомир", "Zhytomyr"),
        ("Закарпаття", "Zakarpattia"),
        ("Медвин", "Medvyn"),
        ("Іршава", "Irshava"),
        ("Їжакевич", "Yizhakevych"),
        ("Кадиївка", "Kadyivka"),
        ("Йосипівка", "Yosypivka"),
        ("Стрий", "Stryi"),
        ("Олексій", "Oleksii"),
        ("Київ", "Kyiv"),
        ("Харків", "Kharkiv"),
        ("Біла Церква", "Bila Tserkva"),
        ("Чернівці", "Chernivtsi"),
        ("Шостка", "Shostka"),
        ("Гоща", "Hoshcha"),
        ("Русь", "Rus"),
        ("Юрій", "Yurii"),
        ("Крюківка", "Kriukivka"),
        ("Яготин", "Yahotyn"),
        ("Ічня", "Ichnia"),
        ("Знам'янка", "Znamianka"),
        ("Наталія Адамчук", "Nataliia Adamchuk"),
    ],
)
def test_ukrainian_follows_kmu_2010(ukrainian: str, latin: str) -> None:
    assert transliterate(ukrainian) == latin


@pytest.mark.parametrize(
    ("name", "latin"),
    [
        ("Сергей Иванов", "Serhei Yvanov"),
        ("Юлия Пушкарёва", "Yulyia Pushkareva"),
        ("Подъезд", "Podezd"),
        ("Мэри", "Mery"),
        ("Выборг", "Vyborh"),
    ],
)
def test_russian_letters_follow_the_ukrainian_table(name: str, latin: str) -> None:
    assert transliterate(name) == latin


@pytest.mark.parametrize(
    ("name", "latin"),
    [
        ("Ёжик", "Ezhyk"),
        ("Ўладзімір", "Uladzimir"),
        ("Ђорђе Јовановић", "Djordje Jovanovyc"),
        ("Ќерка", "Kerka"),
        ("Әсел Құнанбай", "Asel Kunanbai"),
    ],
)
def test_letters_of_other_cyrillic_alphabets_are_written_too(name: str, latin: str) -> None:
    assert transliterate(name) == latin


@pytest.mark.parametrize("apostrophe", ["'", "’", "ʼ", "`"])
def test_apostrophe_inside_a_cyrillic_word_is_dropped(apostrophe: str) -> None:
    assert transliterate(f"Мар{apostrophe}яна") == "Mariana"


def test_apostrophe_outside_a_cyrillic_word_is_kept() -> None:
    assert transliterate("O'Brien, 'Оля'") == "O'Brien, 'Olia'"


def test_word_start_follows_any_non_letter() -> None:
    assert transliterate("Анна-Юлія (Яна)") == "Anna-Yuliia (Yana)"


def test_case_of_each_word_is_kept() -> None:
    assert transliterate("ЮЛІЯ Щука щука") == "YULIIA Shchuka shchuka"


def test_single_capital_letter_is_capitalized() -> None:
    assert transliterate("Я і Ю") == "Ya i Yu"


@pytest.mark.parametrize(
    ("text", "latin"),
    [
        ("Zoë Brontë", "Zoe Bronte"),
        ("Łukasz Żółć", "Lukasz Zolc"),
        ("Søren Æbeltoft", "Soren Aebeltoft"),
        ("Straße", "Strasse"),
    ],
)
def test_other_latin_letters_lose_their_marks(text: str, latin: str) -> None:
    assert transliterate(text) == latin


def test_anything_else_is_kept() -> None:
    assert transliterate("李伟 🦊 42!") == "李伟 🦊 42!"
