"""The `chatko` command line: run the hub, or check its config."""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from chatko import __version__
from chatko.app.check_config import check_config
from chatko.app.run import serve
from chatko.application.config import ExtensionTypes
from chatko.infrastructure.config import read_env_file
from chatko.infrastructure.discovery import discover_extensions

DEFAULT_CONFIG = Path("config/chatko.yaml")
DEFAULT_ENV_FILE = Path(".env")
DEFAULT_DATA = Path("data")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main(argv: Sequence[str] | None = None, *, extensions: ExtensionTypes | None = None) -> int:
    """Run the command line; `extensions` replaces the installed extensions (for tests)."""
    parser = argparse.ArgumentParser(prog="chatko", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="run the hub until it is stopped (SIGTERM or Ctrl+C)")
    _config_arguments(run)
    run.add_argument(
        "--data", type=Path, default=DEFAULT_DATA, help="the hub's state; default: %(default)s"
    )
    run.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="default: %(default)s",
    )
    check = commands.add_parser(
        "check-config", help="validate the config and the routing script, and run its tests"
    )
    _config_arguments(check)
    check.add_argument("--no-tests", action="store_true", help="do not run the routing tests")
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args, extensions)
    if args.command == "check-config":
        return _check_config(args, extensions)
    return 0


def _config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="default: %(default)s")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="variables for ${NAME} (the environment wins); default: %(default)s, if it exists",
    )


def _run(args: argparse.Namespace, extensions: ExtensionTypes | None) -> int:
    logging.basicConfig(level=args.log_level, format=LOG_FORMAT)
    env = _environment(args.env_file)
    types = discover_extensions().types if extensions is None else extensions  # logs refusals
    try:
        return asyncio.run(serve(args.config, env, types, data=args.data))
    except KeyboardInterrupt:  # where the event loop does not handle signals (Windows)
        return 130


def _check_config(args: argparse.Namespace, extensions: ExtensionTypes | None) -> int:
    env = _environment(args.env_file)
    if extensions is None:
        found = discover_extensions()
        for problem in found.problems:
            print(f"warning: {problem}", file=sys.stderr)
        extensions = found.types
    return check_config(args.config, env, extensions, run_tests=not args.no_tests)


def _environment(env_file: Path) -> dict[str, str]:
    """The variables of the `.env` file, if there is one, and of the environment, which wins."""
    env: dict[str, str] = {}
    if env_file.is_file():
        env.update(read_env_file(env_file.read_text(encoding="utf-8")))
    env.update(os.environ)
    return env
