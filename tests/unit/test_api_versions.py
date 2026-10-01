"""Both public APIs follow the same versioning rule (docs/architecture.md §3.4)."""

from collections.abc import Callable

import pytest

from chatko import extension_api, routing_api

APIS = [
    pytest.param(extension_api.API_VERSION, extension_api.is_supported, id="extension_api"),
    pytest.param(routing_api.API_VERSION, routing_api.is_supported, id="routing_api"),
]
type Check = Callable[[tuple[int, int]], bool]


@pytest.mark.parametrize(("version", "is_supported"), APIS)
def test_code_for_the_current_version_is_supported(
    version: tuple[int, int], is_supported: Check
) -> None:
    assert is_supported(version)


@pytest.mark.parametrize(("version", "is_supported"), APIS)
def test_code_for_an_older_minor_version_is_supported(
    version: tuple[int, int], is_supported: Check
) -> None:
    major, _ = version
    assert is_supported((major, 0))


@pytest.mark.parametrize(("version", "is_supported"), APIS)
def test_code_for_a_newer_minor_version_is_not(
    version: tuple[int, int], is_supported: Check
) -> None:
    major, minor = version
    assert not is_supported((major, minor + 1))


@pytest.mark.parametrize(("version", "is_supported"), APIS)
def test_code_for_another_major_version_is_not(
    version: tuple[int, int], is_supported: Check
) -> None:
    major, minor = version
    assert not is_supported((major - 1, minor))
    assert not is_supported((major + 1, 0))
