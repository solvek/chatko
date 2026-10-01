"""The contract suite against the reference extension, `FakeExtension`."""

from typing import Any

from pydantic import BaseModel

from chatko.extension_api import Account, AccountKey, Extension, HubContext
from chatko.extension_api.testing import (
    ContractDriver,
    ExtensionContract,
    FakeConfig,
    FakeExtension,
    FakeNetwork,
    FakePost,
)

HUB = AccountKey("fake", "hub")
ADA = Account(AccountKey("fake", "ada"), "Ada Lovelace")
RECIPIENTS = ("!a1b2c3d4", "!0badc0de")


class FakeExtensionDriver(ContractDriver[FakePost]):
    def __init__(self) -> None:
        self.network = FakeNetwork()

    @property
    def extension_class(self) -> type[Extension[Any]]:
        return FakeExtension

    def config(self) -> dict[str, Any]:
        return {"account": HUB.external_id}

    def endpoint_config(self, place: int) -> dict[str, Any]:
        # Place 0 has recipients, so the suite also covers an endpoint that is delivered to each.
        recipients = list(RECIPIENTS) if place == 0 else []
        return {"place": f"place-{place}", "recipients": recipients}

    def create(self, instance: str, config: BaseModel, hub: HubContext) -> Extension[Any]:
        assert isinstance(config, FakeConfig)
        return FakeExtension(instance, config, hub, network=self.network)

    async def receive(
        self, place: int, text: str, *, by_hub: bool = False, recipient: str | None = None
    ) -> FakePost:
        author = Account(HUB, "chatko") if by_hub else ADA
        if recipient is None and place == 0:
            recipient = RECIPIENTS[0]
        return self.network.post(f"place-{place}", author, text, recipient=recipient)

    async def receive_again(self, post: FakePost) -> None:
        self.network.hand_over(post)

    def posted(self, place: int) -> list[str]:
        return self.network.texts(f"place-{place}", by=HUB)

    def go_offline(self) -> None:
        self.network.online = False


class TestFakeExtensionContract(ExtensionContract):
    def make_driver(self) -> ContractDriver[Any]:
        return FakeExtensionDriver()
