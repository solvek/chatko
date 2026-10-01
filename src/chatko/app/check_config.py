"""`chatko check-config`: validate the config and the routing script without starting the hub."""

import asyncio
import importlib.util
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from chatko.application.config import ConfigError, ExtensionTypes, validate_config
from chatko.application.routing import load_script
from chatko.infrastructure.config import FileConfigLoader
from chatko.routing_api import ScriptError

PYTEST_NO_TESTS = 5
"""The exit code of pytest when it collected nothing."""


def check_config(
    path: Path,
    env: Mapping[str, str],
    types: ExtensionTypes,
    *,
    run_tests: bool = True,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Check the config file, the routing script it names and the admin's tests of the script.

    Prints what it found and returns the exit code: 0 when everything passed, 1 when the config,
    the script or a test is not right.
    """
    out, err = sys.stdout if out is None else out, sys.stderr if err is None else err
    try:
        config = validate_config(asyncio.run(FileConfigLoader(path, env).load()), types)
    except OSError as error:
        print(f"error: cannot read {path}: {error.strerror or error}", file=err)
        return 1
    except ConfigError as error:
        print(f"error: {path} is not valid", file=err)
        for problem in error.errors:
            print(f"  - {problem}", file=err)
        return 1
    print(
        f"ok: {path}: {len(config.extensions)} extensions, {len(config.topology.groups)} groups, "
        f"{len(config.topology.endpoints)} endpoints, {len(config.topology.people)} people",
        file=out,
    )
    directory = path.parent
    if config.routing is None:
        print("ok: no routing script: the default routing", file=out)
    elif not _check_script(directory / config.routing, out, err):
        return 1
    if run_tests:
        return _run_tests(directory, out, err)
    return 0


def _check_script(script: Path, out: TextIO, err: TextIO) -> bool:
    try:
        code = script.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"error: the routing script {script} cannot be read: {error}", file=err)
        return False
    try:
        load_script(code, str(script))
    except ScriptError as error:
        print(f"error: the routing script {script} does not load: {error}", file=err)
        return False
    print(f"ok: the routing script {script} loads", file=out)
    return True


def _run_tests(directory: Path, out: TextIO, err: TextIO) -> int:
    tests = sorted(directory.glob("test_*.py"))
    if not tests:
        print("skipped: no tests of the routing script (test_*.py next to the config)", file=out)
        return 0
    if importlib.util.find_spec("pytest") is None:
        print(
            f"skipped: {len(tests)} test files, but pytest is not installed "
            "(install chatko[test] to run them)",
            file=out,
        )
        return 0
    # The tests run in a process of their own, so that their imports (the routing script) and
    # their failures cannot touch this one.
    done = subprocess.run(  # noqa: S603 - our own interpreter, running the admin's tests
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *map(str, tests)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode == 0:
        print(f"ok: {len(tests)} test files pass", file=out)
        return 0
    print(done.stdout, file=err)
    print(done.stderr, file=err, end="")
    print(
        f"error: the tests of the routing script fail (pytest exit code {done.returncode})",
        file=err,
    )
    return 1
