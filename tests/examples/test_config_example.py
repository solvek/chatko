"""config.example.yaml and routing.example.py are valid together, with stand-in networks."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from chatko.app.check_config import check_config
from chatko.application.config import validate_config
from chatko.extension_api import (
    DeliveryResult,
    EndpointProvider,
    EndpointRef,
    Extension,
    OutboundMessage,
)
from chatko.infrastructure.config import parse_config, read_env_file
from chatko_telegram import TelegramExtension

ROOT = Path(__file__).parents[2]


class Anything(BaseModel):
    """What the real extensions' models check is theirs to test: here anything goes."""

    model_config = ConfigDict(extra="allow")


def stand_in(name: str) -> type[Extension[Any]]:
    class StandIn(Extension[Any], EndpointProvider[Any]):
        type_name: ClassVar[str] = name
        api_version: ClassVar[tuple[int, int]] = (1, 0)
        config_model: ClassVar[type[BaseModel]] = Anything
        endpoint_config_model: ClassVar[type[BaseModel]] = Anything

        def set_endpoints(self, endpoints: Mapping[EndpointRef, Any]) -> None:
            pass

        async def start(self) -> None: ...

        async def stop(self) -> None: ...

        async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
            raise NotImplementedError

    return StandIn


# The extensions written so far check their sections; stand-ins take the others.
TYPES: dict[str, type[Extension[Any]]] = {
    "telegram": TelegramExtension,
    **{name: stand_in(name) for name in ("meshtastic", "briar")},
}


def example_env() -> dict[str, str]:
    names = read_env_file((ROOT / ".env.example").read_text(encoding="utf-8"))
    return {**dict.fromkeys(names, "x"), "TELEGRAM_BOT_TOKEN": "123456:example-token"}


def test_the_example_config_is_valid() -> None:
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")

    config = validate_config(parse_config(text, example_env()), TYPES)

    assert list(config.extensions) == ["tg", "kyiv", "briar"]
    assert [group.name for group in config.topology.groups] == ["family", "street"]
    assert config.topology.endpoint("family.radio").instance == "kyiv"
    assert set(config.topology.sources) == {"longfast", "owner"}
    assert config.admin_endpoint == config.topology.source("owner")
    assert config.topology.people[0].label == "NatAda"
    assert config.routing == "routing.py"


def test_the_example_config_and_script_pass_check_config(tmp_path: Path) -> None:
    (tmp_path / "chatko.yaml").write_text(
        (ROOT / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "routing.py").write_text(
        (ROOT / "routing.example.py").read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert check_config(tmp_path / "chatko.yaml", example_env(), TYPES, run_tests=False) == 0
