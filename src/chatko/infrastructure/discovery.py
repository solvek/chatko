"""Finding the installed extensions in the `chatko.extensions` entry point group."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Protocol, TypeGuard

from chatko.extension_api import Extension, is_supported

_log = logging.getLogger("chatko.discovery")

GROUP = "chatko.extensions"


class EntryPointLike(Protocol):
    """The part of `importlib.metadata.EntryPoint` that discovery uses."""

    @property
    def name(self) -> str: ...

    def load(self) -> object: ...


@dataclass(frozen=True, slots=True)
class Discovery:
    """The extension classes by type name, and why each refused one was refused."""

    types: dict[str, type[Extension[Any]]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def discover_extensions(found: Iterable[EntryPointLike] | None = None) -> Discovery:
    """Load every extension of the entry point group (or of `found`) that this chatko can run.

    An extension is refused, and the reason recorded, when it cannot be imported, is not an
    `Extension` subclass, is registered under a name other than its `type_name`, declares no
    `api_version` as `(major, minor)`, or was written for an extension API version that is not
    supported (D35). One refused extension does not stop the others.
    """
    result = Discovery()
    for entry in entry_points(group=GROUP) if found is None else found:
        problem = _admit(entry, result.types)
        if problem is not None:
            _log.error("extension %s is not available: %s", entry.name, problem)
            result.problems.append(f"extension {entry.name!r}: {problem}")
    return result


def _admit(entry: EntryPointLike, types: dict[str, type[Extension[Any]]]) -> str | None:
    try:
        cls = entry.load()
    except Exception as error:
        return f"cannot be imported: {type(error).__name__}: {error}"
    if not (isinstance(cls, type) and issubclass(cls, Extension)):
        return "is not a subclass of chatko.extension_api.Extension"
    problem = _class_problem(cls, entry.name)
    if problem is not None:
        return problem
    if entry.name in types:
        return "is registered twice"
    types[entry.name] = cls
    return None


def _class_problem(cls: type[Extension[Any]], name: str) -> str | None:
    # A broken class may lack the attributes its base class only declares.
    type_name = getattr(cls, "type_name", None)
    if type_name != name:
        return f"is registered as {name!r} but its type_name is {type_name!r}"
    version = getattr(cls, "api_version", None)
    if not _is_version(version):
        return f"its api_version is not (major, minor) but {version!r}"
    if not is_supported(version):
        return f"was written for extension API {version}, which this chatko does not run"
    return None


def _is_version(value: object) -> TypeGuard[tuple[int, int]]:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, int) and not isinstance(part, bool) for part in value)
    )
