"""Reading `chatko.yaml`: safe YAML load and `${ENV}` substitution (docs/design.md §10)."""

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from chatko.application.config import ConfigError

_VARIABLE = re.compile(r"\$\$\{|\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
"""`${NAME}`, or `$${` for a literal `${`."""


def parse_config(text: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Parse the YAML of a config and replace `${NAME}` in its strings (not its keys) with the
    environment variable NAME. A variable that is not set or empty is an error; the error says
    where it is used, never what the variables hold. `$${` stands for a literal `${`.

    Raises `ConfigError` for YAML that does not parse, a document that is not a mapping or an
    unset variable.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigError([f"the file is not valid YAML: {_first_line(error)}"]) from None
    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise ConfigError(["the file must be a mapping of sections"])
    missing: list[str] = []
    result = _substitute(document, env, "", missing)
    if missing:
        raise ConfigError(missing)
    assert isinstance(result, dict)  # noqa: S101 - a mapping stays one
    return result


def read_env_file(text: str) -> dict[str, str]:
    """The `NAME=value` lines of a `.env` file. Blank lines and `#` comments are skipped, a value
    may be wrapped in one pair of quotes, and `export NAME=value` is accepted."""
    env = {}
    for line in text.splitlines():
        stripped = line.strip().removeprefix("export ").strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[name.strip()] = value
    return env


class FileConfigLoader:
    """Implements `ConfigLoader` over a YAML file and the environment."""

    def __init__(self, path: Path, env: Mapping[str, str]) -> None:
        self.path = path
        self._env = env

    async def load(self) -> dict[str, Any]:
        text = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        return parse_config(text, self._env)


def _substitute(value: object, env: Mapping[str, str], where: str, missing: list[str]) -> object:
    if isinstance(value, str):
        return _VARIABLE.sub(lambda m: _replace(m, env, where, missing), value)
    if isinstance(value, dict):
        return {
            key: _substitute(item, env, f"{where}.{key}" if where else str(key), missing)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_substitute(item, env, f"{where}[{i}]", missing) for i, item in enumerate(value)]
    return value


def _replace(match: re.Match[str], env: Mapping[str, str], where: str, missing: list[str]) -> str:
    name = match["name"]
    if name is None:
        return "${"
    if not env.get(name):
        missing.append(f"{where}: the environment variable {name} is not set or is empty")
        return ""
    return env[name]


def _first_line(error: yaml.YAMLError) -> str:
    # A YAML error may quote the offending line, which may hold a secret: give only the place.
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or "syntax error"
    if mark is None:
        return str(problem)
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"
