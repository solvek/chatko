from pathlib import Path

import pytest

from chatko import __version__
from chatko.app.cli import main
from chatko.extension_api.testing import FakeExtension
from chatko.infrastructure.discovery import Discovery


def test_version_flag_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"chatko {__version__}"


def test_no_arguments_exit_successfully() -> None:
    assert main([]) == 0


def test_check_config_without_a_config_file_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check-config", "--config", str(tmp_path / "missing.yaml")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_check_config_warns_of_the_extensions_it_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "chatko.yaml"
    config.write_text("extensions: {tg: {type: fake}}\n", encoding="utf-8")
    found = Discovery({"fake": FakeExtension}, ["extension 'old': was written for (0, 9)"])
    monkeypatch.setattr("chatko.app.cli.discover_extensions", lambda: found)

    assert main(["check-config", "--config", str(config), "--no-tests"]) == 0
    assert "warning: extension 'old': was written for (0, 9)" in capsys.readouterr().err


def test_ctrl_c_where_the_loop_handles_no_signals_exits_with_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def interrupted(*args: object, **kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("chatko.app.cli.serve", interrupted)

    assert main(["run", "--config", str(tmp_path / "chatko.yaml")], extensions={}) == 130
