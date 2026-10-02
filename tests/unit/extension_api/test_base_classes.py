"""The defaults of the base classes, with a minimal extension that overrides nothing optional."""

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from chatko.extension_api import (
    Delivered,
    DeliveryReport,
    DeliveryResult,
    EndpointProvider,
    EndpointRef,
    Extension,
    OutboundMessage,
)
from chatko.extension_api.testing import FakeHub

ENDPOINT = EndpointRef("minimal", "family.minimal")


class MinimalConfig(BaseModel):
    pass


class MinimalExtension(Extension[MinimalConfig], EndpointProvider[MinimalConfig]):
    type_name: ClassVar[str] = "minimal"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = MinimalConfig
    endpoint_config_model: ClassVar[type[BaseModel]] = MinimalConfig

    def set_endpoints(self, endpoints: Mapping[EndpointRef, MinimalConfig]) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        del endpoint, message
        return Delivered()


def extension() -> MinimalExtension:
    return MinimalExtension("mini", MinimalConfig(), FakeHub())


def test_extension_keeps_its_instance_config_and_hub() -> None:
    hub = FakeHub()
    config = MinimalConfig()

    ext = MinimalExtension("mini", config, hub)

    assert ext.instance == "mini"
    assert ext.config is config
    assert ext.hub is hub


def test_extension_logs_under_its_instance_name() -> None:
    assert extension().logger.name == "chatko.extensions.mini"


def test_an_endpoint_is_one_place_by_default() -> None:
    assert extension().recipients(ENDPOINT) == ()


async def test_delivery_reports_are_ignored_by_default() -> None:
    report = DeliveryReport(
        ENDPOINT, "42", EndpointRef("telegram", "family.telegram"), None, Delivered()
    )

    await extension().delivery_report(report)
