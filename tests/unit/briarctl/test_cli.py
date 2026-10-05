"""The command line around the commands: settings, output, exit codes."""

import io
import runpy
import sys
from pathlib import Path

import pytest

from briarctl import cli
from briarctl.api import BriarClient
from briarctl.testing import FakeBriarClient
from tests.unit.briarctl.harness import TOKEN, briarctl


def connected_to(
    *argv: str, env: dict[str, str] | None = None
) -> tuple[int, list[tuple[str, str]], str]:
    """The exit code, the (url, token) the tool connected with, and what it said on stderr."""
    connections: list[tuple[str, str]] = []
    err = io.StringIO()

    def connect(url: str, token: str) -> BriarClient:
        connections.append((url, token))
        return FakeBriarClient()

    code = cli.run(
        argv,
        env={"BRIAR_AUTH_TOKEN": TOKEN} if env is None else env,
        stdout=io.StringIO(),
        stderr=err,
        confirm=lambda _question: False,
        connect=connect,
    )
    return code, connections, err.getvalue()


def test_it_connects_to_the_local_api_with_the_token_from_the_environment() -> None:
    code, connections, _ = connected_to("link")

    assert (code, connections) == (0, [("http://127.0.0.1:7000", TOKEN)])


def test_the_address_comes_from_the_flag_before_the_environment() -> None:
    env = {"BRIAR_AUTH_TOKEN": TOKEN, "BRIARCTL_URL": "http://from-env:7000"}

    assert connected_to("link", env=env)[1] == [("http://from-env:7000", TOKEN)]
    assert connected_to("link", "--url", "https://hub:8443", env=env)[1] == [
        ("https://hub:8443", TOKEN)
    ]


def test_the_token_comes_from_a_file_with_its_end_of_line_cut_off(tmp_path: Path) -> None:
    file = tmp_path / "auth_token"
    file.write_text("from-file\n", encoding="utf-8")

    code, connections, _ = connected_to("link", "--token-file", str(file), env={})

    assert (code, connections) == (0, [("http://127.0.0.1:7000", "from-file")])


def test_the_file_wins_over_the_environment(tmp_path: Path) -> None:
    file = tmp_path / "auth_token"
    file.write_text("from-file", encoding="utf-8")

    assert connected_to("link", "--token-file", str(file))[1][0][1] == "from-file"


def test_a_token_file_that_cannot_be_read_is_a_settings_error(tmp_path: Path) -> None:
    code, connections, err = connected_to("link", "--token-file", str(tmp_path / "missing"))

    assert (code, connections) == (2, [])
    assert err.startswith("briarctl: cannot read the token file")


@pytest.mark.parametrize("env", [{}, {"BRIAR_AUTH_TOKEN": "  "}])
def test_without_a_token_it_says_where_to_put_it(env: dict[str, str]) -> None:
    code, connections, err = connected_to("link", env=env)

    assert (code, connections) == (2, [])
    assert "no token: set BRIAR_AUTH_TOKEN or give --token-file" in err


@pytest.mark.parametrize(
    ("url", "words"),
    [
        ("ftp://hub", "http(s) URL"),
        ("hub:7000", "http(s) URL"),
        ("http://", "http(s) URL"),
        ("http://user:pass@hub:7000", "user name or a password"),
        ("http://user@hub:7000", "user name or a password"),
    ],
)
def test_an_address_that_is_not_a_plain_http_url_is_a_settings_error(url: str, words: str) -> None:
    code, connections, err = connected_to("link", "--url", url)

    assert (code, connections) == (2, [])
    assert words in err
    assert "pass" not in err.replace("password", "")


def test_json_may_come_before_or_after_the_command() -> None:
    fake = FakeBriarClient()
    fake.create_group("Family")

    before = briarctl(fake, "--json", "group", "list")
    after = briarctl(fake, "group", "list", "--json")

    assert before.json == after.json
    assert before.json[0]["name"] == "Family"


def test_settings_may_come_before_or_after_the_command(tmp_path: Path) -> None:
    file = tmp_path / "auth_token"
    file.write_text("from-file", encoding="utf-8")

    assert connected_to("--token-file", str(file), "link", env={})[1][0][1] == "from-file"
    assert connected_to("link", "--token-file", str(file), env={})[1][0][1] == "from-file"


@pytest.mark.parametrize("argv", [[], ["contact"], ["frobnicate"], ["group", "invite"]])
def test_wrong_arguments_exit_with_2_and_connect_nowhere(argv: list[str]) -> None:
    code, connections, _ = connected_to(*argv)

    assert (code, connections) == (2, [])


def test_help_exits_with_0(capsys: pytest.CaptureFixture[str]) -> None:
    code, connections, _ = connected_to("group", "--help")

    assert (code, connections) == (0, [])
    assert "reveal" in capsys.readouterr().out


def test_every_command_has_help(capsys: pytest.CaptureFixture[str]) -> None:
    commands = [
        ["link"],
        ["contact", "add"], ["contact", "list"], ["contact", "remove"],
        ["invitation", "list"], ["invitation", "accept"], ["invitation", "decline"],
        ["group", "list"], ["group", "members"], ["group", "reveal"],
        ["group", "create"], ["group", "invite"], ["group", "dissolve"],
    ]  # fmt: skip
    for command in commands:
        assert connected_to(*command, "--help")[0] == 0
        assert "usage: briarctl " + " ".join(command) in capsys.readouterr().out


def test_a_failing_command_prints_nothing_on_stdout_and_one_line_on_stderr() -> None:
    fake = FakeBriarClient()

    result = briarctl(fake, "group", "members", "A" * 43)

    assert result.code == 1
    assert result.out == ""
    assert result.err.count("\n") == 1
    assert result.err.startswith("briarctl: ")


def test_main_exits_with_the_code_of_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["briarctl", "--help"])

    with pytest.raises(SystemExit) as stop:
        cli.main()

    assert stop.value.code == 0
    assert "usage: briarctl" in capsys.readouterr().out


def test_main_talks_to_the_api_it_was_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BRIAR_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(sys, "argv", ["briarctl", "--url", "http://127.0.0.1:9", "link"])

    with pytest.raises(SystemExit) as stop:
        cli.main()

    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert "cannot reach briar-headless at http://127.0.0.1:9" in err
    assert TOKEN not in err


def test_python_dash_m_runs_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["briarctl", "--help"])

    with pytest.raises(SystemExit) as stop:
        runpy.run_module("briarctl", run_name="__main__")

    assert stop.value.code == 0


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("y", True), (" YES ", True), ("n", False), ("", False), ("yes please", False)],
)
def test_it_asks_with_input_and_only_yes_counts(
    monkeypatch: pytest.MonkeyPatch, reply: str, *, expected: bool
) -> None:
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return reply

    monkeypatch.setattr("builtins.input", answer)

    assert cli._ask("Remove it?") is expected
    assert prompts == ["Remove it? [y/N] "]


def test_end_of_input_is_no(monkeypatch: pytest.MonkeyPatch) -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)

    assert cli._ask("Remove it?") is False
