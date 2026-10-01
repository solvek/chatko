"""Finding the installed extensions in the `chatko.extensions` entry point group."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Protocol

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
    `Extension` subclass, is registered under a name other than its `type_name`, or was written
    for an extension API version that is not supported (D35). One refused extension does not stop
    the others.
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
    if cls.type_name != entry.name:
        return f"is registered as {entry.name!r} but its type_name is {cls.type_name!r}"
    if not is_supported(cls.api_version):
        return f"was written for extension API {cls.api_version}, which this chatko does not run"
    if entry.name in types:
        return "is registered twice"
    types[entry.name] = cls
    return None
