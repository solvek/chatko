"""The contract test suite: what every extension must do (docs/architecture.md §3.5).

An extension runs it in one of its test modules:

    class TestTelegramContract(ExtensionContract):
        def make_driver(self) -> ContractDriver[Any]:
            return TelegramContractDriver()

pytest collects the inherited tests. The driver connects the suite to the extension and to the fake
of the extension's network port, so the suite runs without a network.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar

import pytest
import pytest_asyncio
from pydantic import BaseModel, ValidationError

from chatko.domain import AccountKey, EndpointRef, MessageId
from chatko.extension_api import is_supported
from chatko.extension_api.delivery import Delivered, DeliveryReport, Failed, Retry
from chatko.extension_api.endpoints import EndpointProvider
from chatko.extension_api.extension import Extension
from chatko.extension_api.hub import HubContext
from chatko.extension_api.messages import InboundMessage, OutboundMessage
from chatko.extension_api.testing.hub import FakeHub

TIMEOUT = 2.0
"""Seconds that any one step of a contract test may take against a fake network."""

INSTANCE = "contract"
FIRST = EndpointRef(INSTANCE, "contract.first")
SECOND = EndpointRef(INSTANCE, "contract.second")
UNKNOWN = EndpointRef(INSTANCE, "contract.unknown")
ELSEWHERE = EndpointRef("other", "contract.elsewhere")


class ContractDriver[P](ABC):
    """Connects the contract suite to one extension and to the fake of its network.

    Places are numbered from 0: different numbers are different places in the network (chats,
    groups, channels). `P` is whatever stands for one post in the fake network.
    """

    @property
    @abstractmethod
    def extension_class(self) -> type[Extension[Any]]:
        """The extension under test. It must also be an `EndpointProvider`."""

    @abstractmethod
    def config(self) -> dict[str, Any]:
        """A valid config section for one instance, as the YAML gives it, without `type`."""

    @abstractmethod
    def endpoint_config(self, place: int) -> dict[str, Any]:
        """A valid config of an endpoint at place `place`, as the YAML gives it, without `ext`."""

    @abstractmethod
    def create(self, instance: str, config: BaseModel, hub: HubContext) -> Extension[Any]:
        """A new instance of the extension, connected to the fake network. `config` is the
        validated `config()`."""

    @abstractmethod
    async def receive(self, place: int, text: str, *, by_hub: bool = False) -> P:
        """Let the network hand the extension a text posted at `place` by another account, or
        by the hub's own account if `by_hub`. Returns the post."""

    @abstractmethod
    async def receive_again(self, post: P) -> None:
        """Let the network hand the extension a post it has handed over before."""

    @abstractmethod
    def posted(self, place: int) -> list[str]:
        """The texts the extension has posted at `place`, oldest first: one per recipient."""

    @abstractmethod
    def go_offline(self) -> None:
        """Make the network unreachable for the extension."""

    async def settle(self) -> None:
        """Wait until the extension has handled all that the network handed it.

        The default lets the event loop run a few rounds; override it when the extension needs
        more, e.g. a poll interval of its fake port.
        """
        for _ in range(20):
            await asyncio.sleep(0)


class ExtensionContract(ABC):
    """The tests every extension passes: the rules of docs/architecture.md §3 that a test can
    check without knowing the network."""

    TIMEOUT: ClassVar[float] = TIMEOUT

    @abstractmethod
    def make_driver(self) -> ContractDriver[Any]:
        """The driver for the extension under test."""

    @pytest.fixture
    def driver(self) -> ContractDriver[Any]:
        return self.make_driver()

    @pytest.fixture
    def hub(self) -> FakeHub:
        return FakeHub(wait_timeout=self.TIMEOUT)

    @pytest.fixture
    def extension(self, driver: ContractDriver[Any], hub: FakeHub) -> Extension[Any]:
        """The extension with one endpoint, at place 0, not started."""
        extension_class = driver.extension_class
        extension = driver.create(
            INSTANCE, extension_class.config_model.model_validate(driver.config()), hub
        )
        _provider(extension).set_endpoints(_endpoints(extension, driver, {FIRST: 0}))
        return extension

    @pytest_asyncio.fixture
    async def running(self, extension: Extension[Any]) -> AsyncIterator[Extension[Any]]:
        async with asyncio.timeout(self.TIMEOUT):
            await extension.start()
        yield extension
        async with asyncio.timeout(self.TIMEOUT):
            await extension.stop()

    # The class.

    def test_type_name_is_a_valid_account_kind(self, driver: ContractDriver[Any]) -> None:
        AccountKey(driver.extension_class.type_name, "any")

    def test_was_written_for_a_supported_api_version(self, driver: ContractDriver[Any]) -> None:
        assert is_supported(driver.extension_class.api_version)

    def test_provides_endpoints(self, driver: ContractDriver[Any]) -> None:
        assert issubclass(driver.extension_class, EndpointProvider)

    def test_config_model_rejects_unknown_keys(self, driver: ContractDriver[Any]) -> None:
        with pytest.raises(ValidationError):
            driver.extension_class.config_model.model_validate(
                {**driver.config(), "no_such_key": 1}
            )

    def test_endpoint_config_model_rejects_unknown_keys(self, driver: ContractDriver[Any]) -> None:
        provider_class = driver.extension_class
        assert issubclass(provider_class, EndpointProvider)
        with pytest.raises(ValidationError):
            provider_class.endpoint_config_model.model_validate(
                {**driver.endpoint_config(0), "no_such_key": 1}
            )

    # Starting and stopping.

    @pytest.mark.asyncio
    async def test_starts_and_stops_without_leaving_tasks(self, extension: Extension[Any]) -> None:
        before = asyncio.all_tasks()
        async with asyncio.timeout(self.TIMEOUT):
            await extension.start()
            await extension.stop()

        left = asyncio.all_tasks() - before
        assert not left, f"tasks still running after stop(): {left}"

    @pytest.mark.asyncio
    async def test_stop_is_safe_without_start(self, extension: Extension[Any]) -> None:
        async with asyncio.timeout(self.TIMEOUT):
            await extension.stop()

    # Reading.

    @pytest.mark.asyncio
    async def test_submits_a_text_that_someone_posted(
        self, running: Extension[Any], driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        await driver.receive(0, "Привіт усім 👋")

        [message] = await hub.wait_for_submissions(
            1,
        )
        assert message.endpoint == FIRST
        assert message.text == "Привіт усім 👋"
        assert message.transport_id
        assert message.author.key.kind == running.type_name

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("running")
    async def test_gives_each_post_its_own_transport_id(
        self, driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        await driver.receive(0, "one")
        await driver.receive(0, "two")

        first, second = await hub.wait_for_submissions(
            2,
        )
        assert first.transport_id != second.transport_id

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("running")
    async def test_keeps_the_transport_id_of_a_post_handed_over_again(
        self, driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        post = await driver.receive(0, "once")
        [first] = await hub.wait_for_submissions(
            1,
        )

        await driver.receive_again(post)
        await driver.settle()

        assert {message.transport_id for message in hub.submitted} == {first.transport_id}

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("running")
    async def test_does_not_submit_the_hubs_own_posts(
        self, driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        await driver.receive(0, "posted by the hub", by_hub=True)
        await driver.receive(0, "marker")

        await self._expect_only(hub, driver, "marker")

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("running")
    async def test_ignores_places_that_are_not_its_endpoints(
        self, driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        await driver.receive(1, "elsewhere")
        await driver.receive(0, "marker")

        await self._expect_only(hub, driver, "marker")

    @pytest.mark.asyncio
    async def test_follows_a_change_of_its_endpoints(
        self, running: Extension[Any], driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        _provider(running).set_endpoints(_endpoints(running, driver, {SECOND: 1}))

        await driver.receive(0, "removed")
        await driver.receive(1, "marker")

        [message] = await self._expect_only(hub, driver, "marker")
        assert message.endpoint == SECOND

    @pytest.mark.asyncio
    async def test_submits_nothing_after_stop(
        self, extension: Extension[Any], driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        async with asyncio.timeout(self.TIMEOUT):
            await extension.start()
            await extension.stop()

        await driver.receive(0, "too late")
        await driver.settle()

        assert hub.submitted == []

    # Delivering.

    @pytest.mark.asyncio
    async def test_delivers_the_label_and_the_text_to_every_recipient(
        self, running: Extension[Any], driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        provider = _provider(running)
        recipients = provider.recipients(FIRST) or (None,)

        for recipient in recipients:
            async with asyncio.timeout(self.TIMEOUT):
                result = await provider.deliver(FIRST, _outbound(hub, recipient))
            assert isinstance(result, Delivered)

        posted = driver.posted(0)
        assert len(posted) == len(recipients)
        assert all("Ada" in text and "Добрий вечір" in text for text in posted)

    @pytest.mark.asyncio
    async def test_answers_retry_while_the_network_is_down(
        self, running: Extension[Any], driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        provider = _provider(running)
        driver.go_offline()

        for recipient in provider.recipients(FIRST) or (None,):
            async with asyncio.timeout(self.TIMEOUT):
                result = await provider.deliver(FIRST, _outbound(hub, recipient))
            assert isinstance(result, Retry)
        assert driver.posted(0) == []

    @pytest.mark.asyncio
    async def test_answers_failed_for_an_endpoint_it_does_not_have(
        self, running: Extension[Any], hub: FakeHub
    ) -> None:
        async with asyncio.timeout(self.TIMEOUT):
            result = await _provider(running).deliver(UNKNOWN, _outbound(hub, None))

        assert isinstance(result, Failed)

    @pytest.mark.asyncio
    async def test_answers_failed_for_a_recipient_it_does_not_have(
        self, running: Extension[Any], hub: FakeHub
    ) -> None:
        async with asyncio.timeout(self.TIMEOUT):
            result = await _provider(running).deliver(FIRST, _outbound(hub, "contract-nobody"))

        assert isinstance(result, Failed)

    @pytest.mark.asyncio
    async def test_takes_delivery_reports(
        self, running: Extension[Any], driver: ContractDriver[Any], hub: FakeHub
    ) -> None:
        await driver.receive(0, "a long story")
        [message] = await hub.wait_for_submissions(
            1,
        )
        report = DeliveryReport(FIRST, message.transport_id, ELSEWHERE, None, Delivered(True))

        async with asyncio.timeout(self.TIMEOUT):
            await _provider(running).delivery_report(report)

    async def _expect_only(
        self, hub: FakeHub, driver: ContractDriver[Any], text: str
    ) -> list[InboundMessage]:
        await hub.wait_for_submissions(
            1,
        )
        await driver.settle()
        assert [message.text for message in hub.submitted] == [text]
        return list(hub.submitted)


def _provider(extension: Extension[Any]) -> EndpointProvider[Any]:
    assert isinstance(extension, EndpointProvider), f"{extension!r} provides no endpoints"
    return extension


def _endpoints(
    extension: Extension[Any], driver: ContractDriver[Any], places: Mapping[EndpointRef, int]
) -> dict[EndpointRef, Any]:
    model = _provider(extension).endpoint_config_model
    return {
        endpoint: model.model_validate(driver.endpoint_config(place))
        for endpoint, place in places.items()
    }


def _outbound(hub: HubContext, recipient: str | None) -> OutboundMessage:
    return OutboundMessage(
        MessageId("contract-1"), "Ada", "Добрий вечір", hub.now(), recipient=recipient
    )
