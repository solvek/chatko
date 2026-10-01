"""The routing script's code from `config/routing.py`."""

from pathlib import Path

import pytest

from chatko.infrastructure.routing_script import ConfiguredScriptSource, FileScriptSource


async def test_reads_the_script(tmp_path: Path) -> None:
    path = tmp_path / "routing.py"
    path.write_text("# Привіт\n", encoding="utf-8")
    source = FileScriptSource(path)

    assert source.origin == str(path)
    assert await source.read() == "# Привіт\n"


async def test_a_missing_file_is_no_script(tmp_path: Path) -> None:
    assert await FileScriptSource(tmp_path / "routing.py").read() is None


async def test_a_file_that_is_not_utf8_cannot_be_read(tmp_path: Path) -> None:
    path = tmp_path / "routing.py"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(UnicodeDecodeError):
        await FileScriptSource(path).read()


async def test_a_directory_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(OSError):  # noqa: PT011 - the error differs between systems
        await FileScriptSource(tmp_path).read()


async def test_the_configured_script_follows_the_name_in_the_config(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("# a\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "b.py").write_text("# b\n", encoding="utf-8")
    name: str | None = "a.py"
    source = ConfiguredScriptSource(tmp_path, lambda: name)

    assert source.origin == str(tmp_path / "a.py")
    assert await source.read() == "# a\n"
    name = "scripts/b.py"
    assert await source.read() == "# b\n"
    name = None
    assert source.origin == "<no routing script>"
    assert await source.read() is None
