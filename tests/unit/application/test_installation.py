"""The installation snapshot the services work with."""

from datetime import timedelta
from typing import ClassVar

import pytest
from pydantic import BaseModel

from chatko.application.installation import Installation
from chatko.extension_api import Extension
from chatko.extension_api.testing import FakeConfig, FakeHub
from tests.unit.application.rig import FAMILY_RADIO, FAMILY_TG, TOPOLOGY, Rig


class LifecycleOnly(Extension[FakeConfig]):
    """An extension with no endpoint capability."""

    type_name: ClassVar[str] = "bare"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = FakeConfig

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def test_the_providers_are_the_running_instances_with_endpoints() -> None:
    rig = Rig()
    installation = Installation(
        TOPOLOGY, {**rig.extensions, "bare": LifecycleOnly("bare", FakeConfig(), FakeHub())}
    )

    assert installation.provider("tg") is rig.extensions["tg"]
    assert installation.provider("bare") is None
    assert installation.provider("gone") is None
    assert installation.extension_types == {"tg": "fake", "mesh": "fake", "bare": "bare"}


def test_recipients_come_from_the_extensions() -> None:
    rig = Rig()

    assert rig.installation.recipients(FAMILY_RADIO) == ("!a1", "!b2")
    assert rig.installation.recipients(FAMILY_TG) == ()
    assert rig.installation.all_recipients() == {FAMILY_RADIO: ("!a1", "!b2")}
    assert Installation(TOPOLOGY).recipients(FAMILY_RADIO) == ()


def test_a_deduplication_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match=r"family\.tg must be positive"):
        Installation(TOPOLOGY, fingerprint_dedup={FAMILY_TG: timedelta(0)})
