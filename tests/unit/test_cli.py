from pathlib import Path

import pytest

from chatko import __version__
from chatko.app.cli import main


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
