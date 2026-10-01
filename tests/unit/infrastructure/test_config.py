from pathlib import Path

import pytest

from chatko.application.config import ConfigError
from chatko.infrastructure.config import FileConfigLoader, parse_config, read_env_file


def test_the_yaml_becomes_plain_data() -> None:
    assert parse_config("a: 1\nb: [x, y]\nc: {d: true}\n", {}) == {
        "a": 1,
        "b": ["x", "y"],
        "c": {"d": True},
    }


def test_an_empty_file_is_an_empty_config() -> None:
    assert parse_config("# nothing\n", {}) == {}


def test_variables_are_replaced_in_values_at_any_depth_but_not_in_keys() -> None:
    text = "${KEY}: 1\nlist: [\"${A}\", {x: 'a ${B} b'}]\nplain: 5\n"

    assert parse_config(text, {"A": "1", "B": "2", "KEY": "k"}) == {
        "${KEY}": 1,
        "list": ["1", {"x": "a 2 b"}],
        "plain": 5,
    }


def test_a_double_dollar_before_a_brace_is_a_literal_dollar_brace() -> None:
    assert parse_config("a: 'cost $${X}'", {"X": "no"}) == {"a": "cost ${X}"}


def test_an_unset_or_empty_variable_is_an_error_naming_its_place_but_no_values() -> None:
    text = "extensions:\n  tg:\n    token: ${TOKEN}\n    names: ['${EMPTY}', '${OTHER}']\n"

    with pytest.raises(ConfigError) as error:
        parse_config(text, {"EMPTY": "", "OTHER": "s3cr3t"})

    assert error.value.errors == (
        "extensions.tg.token: the environment variable TOKEN is not set or is empty",
        "extensions.tg.names[0]: the environment variable EMPTY is not set or is empty",
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a: [unclosed", "the file is not valid YAML"),
        ("- a\n- b\n", "the file must be a mapping of sections"),
        ("just text", "the file must be a mapping of sections"),
        ("a: !!python/object/apply:os.getcwd []", "the file is not valid YAML"),
    ],
)
def test_a_file_that_is_not_a_yaml_mapping_is_an_error(text: str, message: str) -> None:
    with pytest.raises(ConfigError) as error:
        parse_config(text, {})

    assert error.value.errors[0].startswith(message)


def test_a_yaml_error_gives_the_place_and_not_the_text() -> None:
    with pytest.raises(ConfigError) as error:
        parse_config("token: s3cr3t\nbad: [x\n", {})

    assert "line" in error.value.errors[0]
    assert "s3cr3t" not in error.value.errors[0]


def test_a_character_yaml_does_not_allow_is_an_error_with_its_line() -> None:
    with pytest.raises(ConfigError) as error:
        parse_config("a: 1\ntoken: s3\x07cr3t\n", {})

    assert error.value.errors == (
        "the file is not valid YAML: special characters are not allowed (line 2)",
    )


def test_the_env_file_has_name_value_lines() -> None:
    text = (
        "# comment\n\nA=1\nexport B = two words \n"
        "C=\"quoted # not a comment\"\nD='x'\nE=\nnonsense\n"
    )

    assert read_env_file(text) == {
        "A": "1",
        "B": "two words",
        "C": "quoted # not a comment",
        "D": "x",
        "E": "",
    }


async def test_the_loader_reads_the_file_with_the_environment(tmp_path: Path) -> None:
    path = tmp_path / "chatko.yaml"
    path.write_text("token: ${T}\n", encoding="utf-8")

    assert await FileConfigLoader(path, {"T": "abc"}).load() == {"token": "abc"}


async def test_the_loader_raises_oserror_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await FileConfigLoader(tmp_path / "missing.yaml", {}).load()


async def test_the_loader_refuses_a_file_that_is_not_utf8_with_its_line(tmp_path: Path) -> None:
    path = tmp_path / "chatko.yaml"
    path.write_bytes("people:\n  Наталія: [telegram:1]\n".encode("cp1251"))

    with pytest.raises(ConfigError) as error:
        await FileConfigLoader(path, {}).load()

    assert error.value.errors == ("the file is not UTF-8 text (line 2)",)
