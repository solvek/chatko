import io
import textwrap
from pathlib import Path

import pytest

from chatko.app.check_config import check_config
from chatko.app.cli import main
from chatko.extension_api.testing import FakeExtension

TYPES = {"fake": FakeExtension}

CONFIG = """
    extensions:
      tg: {type: fake}
    groups:
      family:
        sites:
          a: {ext: tg, place: a}
          b: {ext: tg, place: b}
    routing: routing.py
"""
SCRIPT = """
    from chatko.routing_api import mirror

    def route(msg, ctx):
        return mirror(msg, ctx)
"""
TEST = """
    from chatko.routing_api.testing import FakeInstallation, assert_routed_to
    import routing

    def test_it_mirrors():
        installation = FakeInstallation(
            extensions={"tg": "fake"}, groups={"family": {"a": "tg", "b": "tg"}}
        )
        msg = installation.message("family.a", "hi")
        assert_routed_to(installation.route(routing, msg), "family.b")
"""


class Run:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.out, self.err = io.StringIO(), io.StringIO()

    def write(self, name: str, text: str) -> None:
        (self.directory / name).write_text(textwrap.dedent(text), encoding="utf-8")

    def check(self, *, run_tests: bool = True, env: dict[str, str] | None = None) -> int:
        return check_config(
            self.directory / "chatko.yaml",
            env or {},
            TYPES,
            run_tests=run_tests,
            out=self.out,
            err=self.err,
        )


@pytest.fixture
def run(tmp_path: Path) -> Run:
    run = Run(tmp_path)
    run.write("chatko.yaml", CONFIG)
    run.write("routing.py", SCRIPT)
    return run


def test_a_valid_config_and_script_pass(run: Run) -> None:
    assert run.check(run_tests=False) == 0

    assert "ok: " in run.out.getvalue()
    assert "1 extensions, 1 groups, 2 endpoints" in run.out.getvalue()
    assert "the routing script" in run.out.getvalue()
    assert run.err.getvalue() == ""


def test_a_config_without_a_routing_script_uses_the_defaults(run: Run) -> None:
    run.write("chatko.yaml", CONFIG.replace("routing: routing.py", ""))

    assert run.check() == 0

    assert "default routing" in run.out.getvalue()
    assert "skipped: no tests" in run.out.getvalue()


def test_every_problem_of_an_invalid_config_is_printed(run: Run) -> None:
    run.write("chatko.yaml", "extensions: {tg: {type: nope}}\nbogus: 1\n")

    assert run.check() == 1

    err = run.err.getvalue()
    assert "bogus: Extra inputs are not permitted" in err
    assert "unknown extension type 'nope'" not in err  # the core's sections fail first


def test_an_unset_variable_is_reported(run: Run) -> None:
    run.write("chatko.yaml", "extensions: {tg: {type: fake, account: '${WHO}'}}\n")

    assert run.check() == 1
    assert "variable WHO is not set" in run.err.getvalue()

    assert run.check(env={"WHO": "me"}) == 0


def test_a_missing_config_file_is_an_error(tmp_path: Path) -> None:
    run = Run(tmp_path)

    assert run.check() == 1
    assert "cannot read" in run.err.getvalue()


def test_a_missing_routing_script_is_an_error(run: Run) -> None:
    (run.directory / "routing.py").unlink()

    assert run.check() == 1
    assert "routing.py cannot be read" in run.err.getvalue()


def test_a_routing_script_that_does_not_load_is_an_error_with_its_line(run: Run) -> None:
    run.write("routing.py", "x = 1\nraise ValueError('boom')\n")

    assert run.check() == 1
    assert "does not load: line 2: ValueError: boom" in run.err.getvalue()


def test_the_admins_tests_run_and_pass(run: Run) -> None:
    run.write("test_routing.py", TEST)

    assert run.check() == 0

    assert "ok: 1 test files pass" in run.out.getvalue()


def test_a_failing_test_fails_the_check_and_shows_pytests_output(run: Run) -> None:
    run.write("test_routing.py", TEST.replace('"family.b"', '"family.a"'))

    assert run.check() == 1

    assert "family.a" in run.err.getvalue()
    assert "the tests of the routing script fail" in run.err.getvalue()


def test_tests_are_skipped_when_pytest_is_not_installed(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.write("test_routing.py", TEST)
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)

    assert run.check() == 0

    assert "pytest is not installed" in run.out.getvalue()


def test_the_command_reads_the_env_file_and_the_environment(
    run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run.write("chatko.yaml", "extensions: {tg: {type: fake, account: '${A}-${B}'}}\n")
    run.write("my.env", "A=from-file\nB=from-file\n")
    monkeypatch.setenv("B", "from-environment")
    args = ["check-config", "--config", str(run.directory / "chatko.yaml"), "--no-tests"]

    assert main([*args, "--env-file", str(run.directory / "my.env")], extensions=TYPES) == 0
    assert main(args, extensions=TYPES) == 1  # no env file: A is not set
    assert "variable A is not set" in capsys.readouterr().err
