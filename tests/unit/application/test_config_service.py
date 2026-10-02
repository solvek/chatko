from typing import Any

from chatko.application.config import ConfigError
from chatko.application.config_service import ConfigOutcome, ConfigService
from chatko.application.testing import InMemoryConfigLoader, RecordingNotices
from chatko.extension_api.testing import FakeExtension

GOOD: dict[str, Any] = {
    "extensions": {"telegram": {"type": "fake"}},
    "sources": {"owner": {"ext": "telegram", "place": "owner"}},
}
BAD: dict[str, Any] = {"extensions": {"telegram": {"type": "nope"}}}


def rig(config: dict[str, Any]) -> tuple[ConfigService, InMemoryConfigLoader, RecordingNotices]:
    loader, notices = InMemoryConfigLoader(config), RecordingNotices()
    service = ConfigService(loader=loader, types={"fake": FakeExtension}, notices=notices)
    return service, loader, notices


async def test_a_valid_config_becomes_current() -> None:
    service, _, notices = rig(GOOD)

    before = service.current
    assert await service.reload() is ConfigOutcome.LOADED

    assert before is None

    assert service.current is not None
    assert list(service.current.extensions) == ["telegram"]
    assert service.errors == ()
    assert notices.notices == []


async def test_the_same_content_changes_nothing() -> None:
    service, _, _ = rig(GOOD)
    await service.reload()
    first = service.current

    assert await service.reload() is ConfigOutcome.UNCHANGED
    assert service.current is first


async def test_an_invalid_new_config_keeps_the_previous_one_and_tells_the_admin() -> None:
    service, loader, notices = rig(GOOD)
    await service.reload()
    first = service.current
    loader.config = BAD

    assert await service.reload() is ConfigOutcome.REFUSED

    assert service.current is first
    assert service.errors == (
        "extensions.telegram: unknown extension type 'nope'; installed: fake",
    )
    [notice] = notices.notices
    assert notice.key == "config:load"
    assert "unknown extension type 'nope'" in notice.text
    assert "The previous config keeps running." in notice.text


async def test_a_problem_is_reported_once_until_it_changes() -> None:
    service, loader, notices = rig(BAD)

    assert await service.reload() is ConfigOutcome.REFUSED
    assert await service.reload() is ConfigOutcome.UNCHANGED
    assert len(notices.notices) == 1
    assert service.current is None
    assert "There is no valid config to run." in notices.notices[0].text

    loader.config = {"extensions": {"telegram": {"type": "nope2"}}}
    await service.reload()
    assert len(notices.notices) == 2


async def test_a_fixed_config_loads_and_a_later_break_is_reported_again() -> None:
    service, loader, notices = rig(BAD)
    await service.reload()
    loader.config = GOOD
    assert await service.reload() is ConfigOutcome.LOADED
    assert service.errors == ()

    loader.config = BAD
    assert await service.reload() is ConfigOutcome.REFUSED
    assert len(notices.notices) == 2


async def test_a_file_that_cannot_be_read_or_parsed_is_refused() -> None:
    service, loader, notices = rig(GOOD)
    await service.reload()
    loader.error = FileNotFoundError(2, "No such file or directory")

    assert await service.reload() is ConfigOutcome.REFUSED
    assert service.current is not None
    assert "No such file or directory" in notices.notices[-1].text

    loader.error = ConfigError(
        ["extensions.telegram.token: the environment variable TOKEN is not set"]
    )
    await service.reload()
    assert "environment variable TOKEN" in notices.notices[-1].text

    loader.error = None
    assert await service.reload() is ConfigOutcome.LOADED  # the same as before, but news again


async def test_a_file_that_still_cannot_be_read_is_reported_once() -> None:
    service, loader, notices = rig(GOOD)
    await service.reload()
    loader.error = PermissionError(13, "Permission denied")

    await service.reload()
    assert await service.reload() is ConfigOutcome.REFUSED

    assert len(notices.notices) == 1


async def test_a_lost_notice_does_not_break_the_reload() -> None:
    service, _, notices = rig(BAD)

    async def broken(text: str, *, key: str) -> None:
        raise RuntimeError("the outbox is gone")

    notices.notify = broken  # type: ignore[method-assign]

    assert await service.reload() is ConfigOutcome.REFUSED
