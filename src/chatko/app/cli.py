"""The `chatko` command line. `run` comes with the wiring (roadmap S15)."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from chatko import __version__
from chatko.app.check_config import check_config
from chatko.application.config import ExtensionTypes
from chatko.infrastructure.config import read_env_file
from chatko.infrastructure.discovery import discover_extensions

DEFAULT_CONFIG = Path("config/chatko.yaml")
DEFAULT_ENV_FILE = Path(".env")


def main(argv: Sequence[str] | None = None, *, extensions: ExtensionTypes | None = None) -> int:
    """Run the command line; `extensions` replaces the installed extensions (for tests)."""
    parser = argparse.ArgumentParser(prog="chatko", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser(
        "check-config", help="validate the config and the routing script, and run its tests"
    )
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="default: %(default)s")
    check.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="variables for ${NAME} (the environment wins); default: %(default)s, if it exists",
    )
    check.add_argument("--no-tests", action="store_true", help="do not run the routing tests")
    args = parser.parse_args(argv)
    if args.command == "check-config":
        return _check_config(args, extensions)
    return 0


def _check_config(args: argparse.Namespace, extensions: ExtensionTypes | None) -> int:
    env: dict[str, str] = {}
    if args.env_file.is_file():
        env.update(read_env_file(args.env_file.read_text(encoding="utf-8")))
    env.update(os.environ)
    if extensions is None:
        found = discover_extensions()
        for problem in found.problems:
            print(f"warning: {problem}", file=sys.stderr)
        extensions = found.types
    return check_config(args.config, env, extensions, run_tests=not args.no_tests)
