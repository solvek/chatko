"""deploy/backup.sh and deploy/restore.sh, with a stand-in for `docker` that records its calls, and
the compose file against the config example (docs/deployment.md)."""

import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
DEPLOY = ROOT / "deploy"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the scripts are POSIX shell")

FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
"""


class Server:
    """A copy of the scripts in a directory like a set-up server, with a fake `docker`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "deploy").mkdir()
        for script in ("backup.sh", "restore.sh"):
            shutil.copy(DEPLOY / script, root / "deploy" / script)
        self.write(".env", "BRIAR_PASSWORD=secret\n")
        self.write("config/chatko.yaml", "extensions: {}\n")
        self.write("data/chatko/chatko.sqlite3", "state\n")
        self.write("data/briar/db/db.mv.db", "account\n")
        bin_dir = root.parent / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        self.log = root.parent / "docker.log"
        self.log.write_text("")
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_LOG": str(self.log),
        }

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["sh", str(self.root / "deploy" / script), *args],  # noqa: S607
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def docker_calls(self) -> list[str]:
        return [re.sub(r"^compose -f \S+ ", "", line) for line in self.log.read_text().splitlines()]

    @property
    def backups(self) -> list[Path]:
        return sorted((self.root / "backups").glob("chatko-*.tar.gz"))


@pytest.fixture
def server(tmp_path: Path) -> Server:
    root = tmp_path / "chatko"
    root.mkdir()
    return Server(root)


def test_a_backup_holds_the_secrets_config_and_data(server: Server) -> None:
    result = server.run("backup.sh")

    assert result.returncode == 0, result.stderr
    [archive] = server.backups
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert {".env", "config/chatko.yaml", "data/briar/db/db.mv.db"} <= set(names)
    assert "data/chatko/chatko.sqlite3" in names
    assert not any(name.startswith("backups") for name in names)


def test_a_backup_is_private(server: Server) -> None:
    server.run("backup.sh")

    [archive] = server.backups
    assert archive.stat().st_mode & 0o077 == 0
    assert (server.root / "backups").stat().st_mode & 0o077 == 0


def test_the_hub_and_briar_stop_for_the_copy_and_start_again(server: Server) -> None:
    server.run("backup.sh")

    assert server.docker_calls() == ["stop chatko briar", "start chatko briar"]


def test_the_stack_starts_again_when_the_copy_fails(server: Server) -> None:
    shutil.rmtree(server.root / "data")

    result = server.run("backup.sh")

    assert result.returncode != 0
    assert server.backups == []
    assert server.docker_calls() == ["stop chatko briar", "start chatko briar"]
    assert list((server.root / "backups").glob("*.part")) == []


def test_the_newest_archives_stay(server: Server) -> None:
    backups = server.root / "backups"
    backups.mkdir()
    for age, name in enumerate(["chatko-1.tar.gz", "chatko-2.tar.gz", "chatko-3.tar.gz"]):
        (backups / name).write_text("old")
        os.utime(backups / name, (1_000_000 - age, 1_000_000 - age))

    server.run("backup.sh", BACKUP_KEEP="2")

    names = {path.name for path in backups.glob("chatko-*.tar.gz")}
    assert len(names) == 2
    assert "chatko-3.tar.gz" not in names


def test_a_backup_goes_to_the_given_directory(server: Server, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"

    server.run("backup.sh", str(elsewhere))

    assert len(list(elsewhere.glob("chatko-*.tar.gz"))) == 1
    assert server.backups == []


def test_a_restore_brings_back_what_was_lost(server: Server) -> None:
    server.run("backup.sh")
    [archive] = server.backups
    shutil.rmtree(server.root / "data")
    shutil.rmtree(server.root / "config")
    (server.root / ".env").unlink()
    server.log.write_text("")

    result = server.run("restore.sh", str(archive))

    assert result.returncode == 0, result.stderr
    assert (server.root / ".env").read_text() == "BRIAR_PASSWORD=secret\n"
    assert (server.root / "config/chatko.yaml").read_text() == "extensions: {}\n"
    assert (server.root / "data/briar/db/db.mv.db").read_text() == "account\n"
    assert (server.root / "deploy/.env").resolve() == (server.root / ".env").resolve()
    assert server.docker_calls() == ["down"]
    assert list(server.root.glob("replaced-*")) == []


def test_a_restore_keeps_what_it_replaces(server: Server) -> None:
    server.run("backup.sh")
    [archive] = server.backups
    server.write("data/chatko/chatko.sqlite3", "newer state\n")
    server.write("config/chatko.yaml", "extensions: {changed: true}\n")

    result = server.run("restore.sh", str(archive))

    assert result.returncode == 0, result.stderr
    assert (server.root / "data/chatko/chatko.sqlite3").read_text() == "state\n"
    [old] = server.root.glob("replaced-*")
    assert (old / "data/chatko/chatko.sqlite3").read_text() == "newer state\n"
    assert (old / "config/chatko.yaml").read_text() == "extensions: {changed: true}\n"
    assert (old / ".env").read_text() == "BRIAR_PASSWORD=secret\n"


@pytest.mark.parametrize("kind", ["missing", "not an archive", "not a backup"])
def test_a_restore_refuses_what_is_not_a_backup_and_changes_nothing(
    server: Server, tmp_path: Path, kind: str
) -> None:
    archive = tmp_path / "other.tar.gz"
    if kind == "not an archive":
        archive.write_text("text")
    elif kind == "not a backup":
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(server.root / "config", arcname="config")
    server.log.write_text("")

    result = server.run("restore.sh", str(archive))

    assert result.returncode == 1
    assert "restore:" in result.stderr
    assert server.docker_calls() == []
    assert (server.root / "data/briar/db/db.mv.db").read_text() == "account\n"
    assert list(server.root.glob("replaced-*")) == []


def test_a_restore_needs_an_archive(server: Server) -> None:
    assert server.run("restore.sh").returncode != 0


def test_the_hub_container_gets_every_variable_of_the_config_example() -> None:
    compose = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    given = set(compose["services"]["chatko"]["environment"])
    lines = (ROOT / "config.example.yaml").read_text().splitlines()
    wanted = {
        name
        for line in lines
        if not line.lstrip().startswith("#")
        for name in re.findall(r"\$\{(\w+)\}", line)
    }

    assert wanted <= given


def test_setup_makes_every_secret_that_the_compose_file_requires() -> None:
    compose = (DEPLOY / "docker-compose.yml").read_text()
    setup = (DEPLOY / "setup.sh").read_text()
    required = set(re.findall(r"\$\{(\w+):\?", compose))

    assert required
    for name in required:
        assert name in setup, name
