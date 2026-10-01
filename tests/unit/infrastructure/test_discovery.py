from typing import Any, ClassVar

from pydantic import BaseModel

from chatko.extension_api import Extension
from chatko.extension_api.testing import FakeConfig, FakeExtension
from chatko.infrastructure.discovery import discover_extensions


class Entry:
    def __init__(self, name: str, loaded: object = None, error: Exception | None = None) -> None:
        self.name, self._loaded, self._error = name, loaded, error

    def load(self) -> object:
        if self._error is not None:
            raise self._error
        return self._loaded


def extension(type_name: str, api_version: tuple[int, int]) -> type[Extension[Any]]:
    class Other(FakeExtension):
        pass

    Other.type_name = type_name
    Other.api_version = api_version
    return Other


def test_supported_extensions_are_found_by_type_name() -> None:
    found = discover_extensions([Entry("fake", FakeExtension)])

    assert found.types == {"fake": FakeExtension}
    assert found.problems == []


def test_an_unsupported_api_version_is_refused_and_the_others_still_load() -> None:
    newer = extension("future", (2, 0))
    found = discover_extensions([Entry("future", newer), Entry("fake", FakeExtension)])

    assert found.types == {"fake": FakeExtension}
    assert len(found.problems) == 1
    assert found.problems[0].startswith("extension 'future': was written for extension API (2, 0)")


def test_every_kind_of_broken_extension_is_refused_with_a_reason() -> None:
    class NotAnExtension:
        config_model: ClassVar[type[BaseModel]] = BaseModel

    found = discover_extensions(
        [
            Entry("broken", error=ImportError("no module named x")),
            Entry("plain", NotAnExtension),
            Entry("value", 42),
            Entry("renamed", FakeExtension),
            Entry("fake", FakeExtension),
            Entry("fake", FakeExtension),
        ]
    )

    assert list(found.types) == ["fake"]
    assert found.problems == [
        "extension 'broken': cannot be imported: ImportError: no module named x",
        "extension 'plain': is not a subclass of chatko.extension_api.Extension",
        "extension 'value': is not a subclass of chatko.extension_api.Extension",
        "extension 'renamed': is registered as 'renamed' but its type_name is 'fake'",
        "extension 'fake': is registered twice",
    ]


def bare(**attributes: object) -> type[Extension[Any]]:
    """An extension class with only the given class attributes."""

    class Bare(Extension[FakeConfig]):
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    for name, value in attributes.items():
        setattr(Bare, name, value)
    return Bare


def test_an_extension_without_a_name_or_a_valid_version_is_refused() -> None:
    found = discover_extensions(
        [
            Entry("nameless", bare(api_version=(1, 0))),
            Entry("unversioned", bare(type_name="unversioned")),
            Entry("short", bare(type_name="short", api_version=(1,))),
            Entry("text", bare(type_name="text", api_version="1.0")),
            Entry("fake", FakeExtension),
        ]
    )

    assert list(found.types) == ["fake"]
    assert found.problems == [
        "extension 'nameless': is registered as 'nameless' but its type_name is None",
        "extension 'unversioned': its api_version is not (major, minor) but None",
        "extension 'short': its api_version is not (major, minor) but (1,)",
        "extension 'text': its api_version is not (major, minor) but '1.0'",
    ]


def test_without_entry_points_the_installed_ones_are_used() -> None:
    found = discover_extensions()

    assert isinstance(found.types, dict)
