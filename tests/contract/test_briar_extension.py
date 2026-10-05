"""The contract suite against the Briar extension, over the fake `briar-headless`."""

from typing import Any

from pydantic import BaseModel

from chatko.extension_api import Extension, HubContext
from chatko.extension_api.testing import ContractDriver, ExtensionContract
from chatko_briar import BriarConfig, BriarExtension, ids
from chatko_briar.api import GroupMessage, MessageAdded
from chatko_briar.testing import FakeBriarApi, make_id

PLACES = 3


def group_id(place: int) -> bytes:
    return make_id(f"group {place}")


class BriarDriver(ContractDriver[GroupMessage]):
    def __init__(self) -> None:
        self.api = FakeBriarApi()
        for place in range(PLACES):
            self.api.add_group(group_id(place), f"Group {place}")

    @property
    def extension_class(self) -> type[Extension[Any]]:
        return BriarExtension

    def config(self) -> dict[str, Any]:
        return {"api": "http://briar:7000", "auth_token": "contract-secret"}

    def endpoint_config(self, place: int) -> dict[str, Any]:
        return {"group": ids.to_text(group_id(place))}

    def create(self, instance: str, config: BaseModel, hub: HubContext) -> Extension[Any]:
        assert isinstance(config, BriarConfig)
        return BriarExtension(instance, config, hub, api=self.api, backoff=(0.01, 0.05))

    async def receive(
        self, place: int, text: str, *, by_hub: bool = False, recipient: str | None = None
    ) -> GroupMessage:
        return self.api.arrive(group_id(place), text, own=by_hub)

    async def receive_again(self, post: GroupMessage) -> None:
        self.api.push(MessageAdded(post))

    def posted(self, place: int) -> list[str]:
        return self.api.texts(group_id(place))

    def go_offline(self) -> None:
        self.api.online = False

    async def settle(self) -> None:
        await super().settle()
        await self.api.idle()


class TestBriarContract(ExtensionContract):
    def make_driver(self) -> ContractDriver[Any]:
        return BriarDriver()
