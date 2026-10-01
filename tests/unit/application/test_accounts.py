"""The admin notice for an account seen for the first time (design.md §8)."""

from datetime import datetime

import pytest

from chatko.application.accounts import NewAccounts
from chatko.application.testing import (
    FakeClock,
    InMemoryAccounts,
    RecordedNotice,
    RecordingNotices,
)
from chatko.domain import Account, AccountKey
from tests.unit.application.rig import ADA, FAMILY_CHANNEL, NAT, TOPOLOGY, Rig

RADIO = Account(AccountKey("fake", "!a1b2c3d4"), "Base Camp", "BC1")
SILENT = Account(AccountKey("fake", "!0badc0de"))


def watch(*, enabled: bool = True) -> tuple[NewAccounts, InMemoryAccounts, RecordingNotices]:
    registry, notices = InMemoryAccounts(), RecordingNotices()
    accounts = NewAccounts(
        registry=registry,
        notices=notices,
        clock=FakeClock(),
        topology=lambda: TOPOLOGY,
        enabled=lambda: enabled,
    )
    return accounts, registry, notices


async def test_a_new_account_is_told_once_with_its_names() -> None:
    accounts, _, notices = watch()

    await accounts.saw(RADIO, FAMILY_CHANNEL)
    await accounts.saw(RADIO, FAMILY_CHANNEL)

    assert notices.notices == [
        RecordedNotice(
            "A new account at family.channel: fake:!a1b2c3d4 (Base Camp / BC1). To give it a "
            "label of its own, add it to a person in `people`.",
            "account:fake:!a1b2c3d4",
        )
    ]


async def test_an_account_heard_nowhere_in_particular_and_without_names() -> None:
    accounts, _, notices = watch()

    await accounts.saw(SILENT)

    assert notices.notices[0].text.startswith("A new account: fake:!0badc0de. To give")


async def test_a_person_of_the_config_is_noted_but_not_told() -> None:
    accounts, registry, notices = watch()

    await accounts.saw(NAT, FAMILY_CHANNEL)

    assert NAT.key in registry.accounts
    assert notices.notices == []


async def test_nothing_is_told_unless_the_config_asks_for_it() -> None:
    accounts, registry, notices = watch(enabled=False)

    await accounts.saw(ADA, FAMILY_CHANNEL)

    assert ADA.key in registry.accounts
    assert notices.notices == []


async def test_a_failing_registry_is_logged_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    accounts, registry, _ = watch()

    async def broken(account: Account, at: datetime) -> bool:
        raise OSError("disk full")

    registry.note = broken  # type: ignore[method-assign]
    await accounts.saw(ADA)

    assert "noting the account fake:ada failed" in caplog.text


async def test_the_extension_hub_shows_it_the_accounts_heard() -> None:
    rig = Rig()
    accounts, registry, _ = watch()
    await rig.hub("mesh", accounts).heard(RADIO, FAMILY_CHANNEL)

    assert RADIO.key in registry.accounts
