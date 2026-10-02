"""The running hub: start, config reloads, the extension instances' lifecycle and stop
(architecture.md §3.1, §5.5), over in-memory fakes."""

import copy
from collections import defaultdict
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from chatko.application.config import Config
from chatko.application.config_service import ConfigOutcome
from chatko.application.hub import ExtensionHub
from chatko.application.outbox import OutboxSettings
from chatko.application.routing import ReloadOutcome
from chatko.application.runtime import HubPorts, HubRuntime, HubSettings, StartError
from chatko.application.testing import (
    FakeClock,
    InMemoryAccounts,
    InMemoryConfigLoader,
    InMemoryHistoryStore,
    InMemoryScriptSource,
    InMemoryStore,
    SequentialIds,
)
from chatko.domain import (
    Account,
    AccountKey,
    Author,
    DeliveryState,
    EndpointRef,
    Message,
    MessageId,
    fingerprint,
)
from chatko.extension_api import Extension, HubContext, InboundMessage
from chatko.extension_api.testing import FakeConfig, FakeEndpointConfig, FakeExtension, FakeNetwork
from tests.unit.application.rig import ADA, NAT, settle

FAMILY_TG = EndpointRef("telegram", "family.telegram")
FAMILY_RADIO = EndpointRef("mesh", "family.radio")
OWNER = EndpointRef("telegram", "owner")

CONFIG: dict[str, Any] = {
    "extensions": {"telegram": {"type": "fake"}, "mesh": {"type": "fake"}},
    "groups": {
        "family": {
            "sites": {
                "telegram": {"ext": "telegram", "place": "family"},
                "radio": {"ext": "mesh", "place": "family-dm", "recipients": ["!a1", "!b2"]},
            }
        }
    },
    "sources": {"owner": {"ext": "telegram", "place": "owner"}},
    "admin_notices": {"to": "owner"},
    "people": {"NatAda": ["fake:nat"]},
}

TO_OWNER = """
from chatko.routing_api import to_endpoint

def route(msg, ctx):
    return [to_endpoint(ctx.source("owner"))]
"""

HEARD_BY_OWNER = """
from chatko.routing_api import AccountKey, to_endpoint

def route(msg, ctx):
    heard = ctx.last_heard(AccountKey("fake", "ada"))
    return [to_endpoint(ctx.source("owner"), text=f"{msg.text} ({heard:%H:%M})")]
"""


def edited(**sections: Any) -> dict[str, Any]:
    raw = copy.deepcopy(CONFIG)
    raw.update(sections)
    return raw


class Tracked(FakeExtension):
    """A `FakeExtension` on its rig's network for the instance, which a test can make fail."""

    rig: ClassVar["Rig"]

    def __init__(self, instance: str, config: FakeConfig, hub: HubContext) -> None:
        super().__init__(instance, config, hub, network=self.rig.networks[instance])
        self.started = self.stopped = False
        if isinstance(hub, ExtensionHub):  # not only checked by validate_config
            self.rig.created.append(self)

    async def start(self) -> None:
        if self.instance in self.rig.failing_start:
            raise RuntimeError("no network")
        await super().start()
        self.started = True

    async def stop(self) -> None:
        late = self.rig.submit_on_stop.get(self.instance)
        if late is not None:
            await self.hub.submit(late)
        notice = self.rig.notice_on_stop.get(self.instance)
        if notice is not None:
            await self.hub.notify_admin(notice)
        await super().stop()
        self.stopped = True
        if self.instance in self.rig.failing_stop:
            raise RuntimeError("stuck")


class LifecycleOnly(Extension[FakeConfig]):
    """An extension without endpoints."""

    type_name: ClassVar[str] = "bare"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = FakeConfig

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class Rig:
    """A `HubRuntime` over in-memory ports, with `FakeExtension` instances on fake networks that
    outlive the instances (one network per instance name)."""

    def __init__(
        self,
        raw: Mapping[str, Any] = CONFIG,
        *,
        script: str | None = None,
        store: InMemoryStore | None = None,
        clock: FakeClock | None = None,
        networks: Mapping[str, FakeNetwork] | None = None,
        history: InMemoryHistoryStore | None = None,
    ) -> None:
        self.clock = FakeClock() if clock is None else clock
        self.store = InMemoryStore() if store is None else store
        self.history = InMemoryHistoryStore() if history is None else history
        self.accounts = InMemoryAccounts()
        self.loader = InMemoryConfigLoader(copy.deepcopy(dict(raw)))
        self.script = InMemoryScriptSource(script)
        self.networks: defaultdict[str, FakeNetwork] = defaultdict(FakeNetwork, networks or {})
        self.failing_start: set[str] = set()
        self.failing_stop: set[str] = set()
        self.submit_on_stop: dict[str, InboundMessage] = {}
        self.notice_on_stop: dict[str, str] = {}
        self.created: list[Tracked] = []
        owner = self

        class Fake(Tracked):
            rig = owner

        self.fake_type = Fake
        self.runtime = HubRuntime(
            ports=HubPorts(
                clock=self.clock,
                ids=SequentialIds(),
                messages=self.store,
                outbox=self.store,
                history=self.history,
                accounts=self.accounts,
                config=self.loader,
                script=self.script,
            ),
            types={"fake": Fake, "bare": LifecycleOnly},
            settings=HubSettings(outbox=OutboxSettings(stop_grace=timedelta(0))),
        )

    def instance(self, name: str) -> Tracked:
        extension = self.runtime.extensions[name]
        assert isinstance(extension, Tracked)
        return extension

    def post(self, endpoint: EndpointRef, author: Account, text: str, **kw: Any) -> None:
        self.networks[endpoint.instance].post(self.place(endpoint), author, text, **kw)

    def place(self, endpoint: EndpointRef) -> str:
        config = self.runtime.config
        assert config is not None
        settings = config.endpoints[endpoint.instance][endpoint]
        assert isinstance(settings, FakeEndpointConfig)
        return settings.place

    def posted(self, instance: str, place: str, *, recipient: str | None = None) -> list[str]:
        """What the hub posted at `place` of the instance's network (to `recipient`)."""
        hub = AccountKey("fake", "hub")
        return [
            post.text
            for post in self.networks[instance].posts
            if post.place == place and post.author.key == hub and post.recipient == recipient
        ]

    def notices(self) -> list[str]:
        """The admin notices the hub posted in the owner's chat."""
        return self.posted("telegram", "owner")

    async def reload(self, raw: Mapping[str, Any]) -> ConfigOutcome:
        self.loader.config = copy.deepcopy(dict(raw))
        outcome = (await self.runtime.refresh()).config
        await settle()
        return outcome

    async def advance(self, delta: timedelta) -> None:
        self.clock.advance(delta)
        await settle()


def config_of(rig: Rig) -> Config:
    assert rig.runtime.config is not None
    return rig.runtime.config


class TestStart:
    async def test_without_a_valid_config_the_hub_does_not_start(self) -> None:
        rig = Rig({"extensions": {"telegram": {"type": "nope"}}})

        with pytest.raises(StartError, match="unknown extension type 'nope'") as raised:
            await rig.runtime.start()

        assert raised.value.errors[0].startswith("extensions.telegram:")
        assert rig.runtime.config is None
        assert not rig.created
        await rig.runtime.stop()  # nothing to stop

    async def test_every_instance_runs_with_its_endpoints(self) -> None:
        rig = Rig()
        await rig.runtime.start()

        assert list(rig.runtime.extensions) == ["telegram", "mesh"]
        assert all(extension.started for extension in rig.created)
        installation = rig.runtime.installation()
        assert installation.running == {"telegram", "mesh"}
        assert installation.recipients(FAMILY_RADIO) == ("!a1", "!b2")
        assert installation.topology is config_of(rig).topology
        await rig.runtime.stop()

    async def test_a_message_crosses_from_one_site_to_the_others(self) -> None:
        rig = Rig()
        await rig.runtime.start()

        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        assert rig.posted("mesh", "family-dm", recipient="!a1") == ["NatAda: Привіт"]
        assert rig.posted("mesh", "family-dm", recipient="!b2") == ["NatAda: Привіт"]
        assert len(rig.instance("telegram").reports) == 2
        await rig.runtime.stop()

    async def test_it_cannot_start_twice(self) -> None:
        rig = Rig()
        await rig.runtime.start()

        with pytest.raises(RuntimeError, match="running already"):
            await rig.runtime.start()
        await rig.runtime.stop()

    async def test_the_routing_script_runs_from_the_first_message(self) -> None:
        rig = Rig(script=TO_OWNER)
        await rig.runtime.start()

        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        assert rig.runtime.routing.script is not None
        assert rig.posted("telegram", "owner") == ["NatAda: Привіт"]
        assert not rig.posted("mesh", "family-dm", recipient="!a1")
        await rig.runtime.stop()

    async def test_a_refused_routing_script_at_start_reaches_the_admin(self) -> None:
        rig = Rig(script="def route(:\n")
        await rig.runtime.start()
        await settle()

        [notice] = rig.notices()
        assert "was not loaded" in notice
        await rig.runtime.stop()

    async def test_a_refused_routing_script_at_start_reaches_each_recipient_of_the_admin(
        self,
    ) -> None:
        rig = Rig(edited(admin_notices={"to": "family.radio"}), script="def route(:\n")
        await rig.runtime.start()
        await settle()

        for node in ("!a1", "!b2"):
            [notice] = rig.posted("mesh", "family-dm", recipient=node)
            assert "was not loaded" in notice
        await rig.runtime.stop()

    async def test_the_deliveries_pending_from_before_are_delivered(self) -> None:
        first = Rig()
        await first.runtime.start()
        first.networks["mesh"].online = False
        first.post(FAMILY_TG, NAT, "1")
        await settle()
        await first.runtime.stop()

        first.networks["mesh"].online = True
        second = Rig(store=first.store, clock=first.clock, networks=first.networks)
        await second.runtime.start()
        await settle()

        assert second.posted("mesh", "family-dm", recipient="!a1") == ["NatAda: 1"]
        await second.runtime.stop()

    async def test_the_routing_history_is_restored(self) -> None:
        history = InMemoryHistoryStore()
        earlier = FakeClock().now() - timedelta(hours=1)
        history.heard[(ADA.key, None)] = earlier
        rig = Rig(history=history, script=HEARD_BY_OWNER)
        await rig.runtime.start()

        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        assert rig.posted("telegram", "owner") == [f"NatAda: Привіт ({earlier:%H:%M})"]
        await rig.runtime.stop()

    async def test_a_failure_while_starting_stops_what_had_started(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = Rig()

        async def broken(*args: object) -> Any:
            raise OSError("disk full")

        monkeypatch.setattr(rig.store, "pending", broken)
        with pytest.raises(OSError, match="disk full"):
            await rig.runtime.start()

        assert all(extension.stopped for extension in rig.created)
        await rig.runtime.stop()  # stopped already

    async def test_an_extension_without_endpoints_runs_too(self) -> None:
        rig = Rig(edited(extensions={**CONFIG["extensions"], "clock": {"type": "bare"}}))
        await rig.runtime.start()

        assert rig.runtime.installation().is_running("clock")
        assert isinstance(rig.runtime.extensions["clock"], LifecycleOnly)
        await rig.runtime.stop()


class TestInstancesThatFail:
    async def test_an_instance_that_does_not_start_waits_and_the_admin_is_told(self) -> None:
        rig = Rig()
        rig.failing_start.add("mesh")
        await rig.runtime.start()

        rig.post(FAMILY_TG, NAT, "1")
        await settle()

        assert rig.runtime.installation().running == {"telegram"}
        assert any("mesh did not start: RuntimeError: no network" in n for n in rig.notices())
        assert {d.state for d in rig.store.deliveries if d.endpoint == FAMILY_RADIO} == {
            DeliveryState.PENDING
        }
        await rig.runtime.stop()

    async def test_it_is_tried_again_when_the_config_changes(self) -> None:
        rig = Rig()
        rig.failing_start.add("mesh")
        await rig.runtime.start()
        rig.post(FAMILY_TG, NAT, "1")
        await settle()

        rig.failing_start.clear()
        await rig.reload(edited(people={}))

        assert rig.runtime.installation().running == {"telegram", "mesh"}
        # Labelled when it came, by the person of the config then.
        assert rig.posted("mesh", "family-dm", recipient="!a1") == ["NatAda: 1"]
        await rig.runtime.stop()

    async def test_an_instance_that_cannot_be_created_is_left_out(self) -> None:
        rig = Rig()

        def refuse(self: FakeExtension, endpoints: Mapping[EndpointRef, Any]) -> None:
            if isinstance(self.hub, ExtensionHub):  # validate_config's check passes
                raise ValueError("the node is gone")
            FakeExtension.set_endpoints(self, endpoints)

        rig.fake_type.set_endpoints = refuse  # type: ignore[method-assign]
        await rig.runtime.start()
        await settle()

        assert rig.runtime.extensions == {}
        assert rig.runtime.installation().running == frozenset()
        await rig.runtime.stop()

    async def test_an_instance_that_raises_while_stopping_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.failing_stop.add("mesh")

        await rig.runtime.stop()

        assert "mesh raised while stopping" in caplog.text
        assert rig.instance("telegram").stopped is True


class TestReload:
    async def test_a_new_site_is_given_to_the_running_instance(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        telegram = rig.instance("telegram")
        groups = copy.deepcopy(CONFIG["groups"])
        groups["family"]["sites"]["chat"] = {"ext": "mesh", "place": "family-ch"}

        assert await rig.reload(edited(groups=groups)) is ConfigOutcome.LOADED
        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        assert rig.instance("telegram") is telegram
        assert rig.posted("mesh", "family-ch") == ["NatAda: Привіт"]
        await rig.runtime.stop()

    async def test_an_instance_whose_section_changed_is_restarted(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        old = rig.instance("mesh")
        extensions = copy.deepcopy(CONFIG["extensions"])
        extensions["mesh"]["account"] = "hub2"

        await rig.reload(edited(extensions=extensions))

        new = rig.instance("mesh")
        assert new is not old
        assert old.stopped is True
        assert new.started is True
        assert rig.instance("telegram").stopped is False
        await rig.runtime.stop()

    async def test_a_removed_instance_stops_and_deliveries_to_its_endpoints_fail(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.networks["mesh"].online = False
        rig.post(FAMILY_TG, NAT, "1")
        await settle()
        mesh = rig.instance("mesh")

        await rig.reload(
            edited(
                extensions={"telegram": {"type": "fake"}},
                groups={"family": {"sites": {"telegram": {"ext": "telegram", "place": "family"}}}},
            )
        )
        await rig.advance(timedelta(minutes=1))

        assert mesh.stopped is True
        assert "mesh" not in rig.runtime.extensions
        to_radio = [d for d in rig.store.deliveries if d.endpoint == FAMILY_RADIO]
        assert {d.state for d in to_radio} == {DeliveryState.FAILED}
        assert {d.last_error for d in to_radio} == {"family.radio is no longer in the config"}
        await rig.runtime.stop()

    async def test_a_notice_while_a_reload_stops_an_instance_goes_by_the_running_config(
        self,
    ) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.notice_on_stop["mesh"] = "mesh is going"
        extensions = copy.deepcopy(CONFIG["extensions"])
        extensions["mesh"]["account"] = "hub2"
        sources = {**CONFIG["sources"], "admin": {"ext": "telegram", "place": "admin"}}

        await rig.reload(
            edited(extensions=extensions, sources=sources, admin_notices={"to": "admin"})
        )

        assert rig.notices() == ["chatko: mesh is going"]
        await rig.runtime.stop()

    async def test_an_invalid_config_keeps_the_running_one(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        config = rig.runtime.config

        assert await rig.reload(edited(admin_notices={"to": "nowhere"})) is ConfigOutcome.REFUSED

        assert rig.runtime.config is config
        assert any("The config was not loaded" in notice for notice in rig.notices())
        await rig.runtime.stop()

    async def test_the_same_config_changes_nothing(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        instances = dict(rig.runtime.extensions)

        assert await rig.reload(CONFIG) is ConfigOutcome.UNCHANGED

        assert dict(rig.runtime.extensions) == instances
        await rig.runtime.stop()

    async def test_a_new_routing_script_is_loaded(self) -> None:
        rig = Rig()
        await rig.runtime.start()

        rig.script.code = TO_OWNER
        refresh = await rig.runtime.refresh()
        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        assert refresh.routing is ReloadOutcome.LOADED
        assert rig.posted("telegram", "owner") == ["NatAda: Привіт"]
        await rig.runtime.stop()

    async def test_a_refresh_before_start_or_after_stop_does_nothing(self) -> None:
        rig = Rig()

        refresh = await rig.runtime.refresh()

        assert refresh.config is ConfigOutcome.UNCHANGED
        assert refresh.routing is None
        assert rig.runtime.config is None

    async def test_a_running_instance_that_refuses_its_new_endpoints_keeps_running(
        self,
    ) -> None:
        rig = Rig()
        await rig.runtime.start()
        telegram = rig.instance("telegram")

        def refuse(endpoints: Mapping[EndpointRef, Any]) -> None:
            raise ValueError("no such chat")

        telegram.set_endpoints = refuse  # type: ignore[method-assign]
        groups = copy.deepcopy(CONFIG["groups"])
        groups["family"]["sites"]["telegram"]["place"] = "family2"
        await rig.reload(edited(groups=groups))

        assert rig.instance("telegram") is telegram
        assert rig.runtime.installation().is_running("telegram")
        assert any("telegram did not take its new endpoints" in text for text in rig.notices())
        await rig.runtime.stop()


class TestStop:
    async def test_it_stops_every_instance_and_saves_the_history(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        await rig.runtime.stop()

        assert all(extension.stopped for extension in rig.created)
        assert rig.history.heard[(NAT.key, FAMILY_TG)] == rig.clock.now()
        assert rig.history.arrivals
        assert rig.runtime.installation().running == frozenset()

    async def test_messages_submitted_while_stopping_are_delivered_after_the_next_start(
        self,
    ) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.submit_on_stop["telegram"] = InboundMessage(FAMILY_TG, "t-late", ADA, "late")
        await rig.runtime.stop()
        # The worker stopped before the instances: the late message is stored, not delivered.
        assert not rig.posted("mesh", "family-dm", recipient="!a1")
        assert len(rig.store.messages) == 1

        second = Rig(store=rig.store, clock=rig.clock, networks=rig.networks)
        await second.runtime.start()
        await settle()

        assert second.posted("mesh", "family-dm", recipient="!a1") == ["AdaLov: late"]
        await second.runtime.stop()


class TestUpkeep:
    async def test_the_history_is_saved_every_ten_seconds(self) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()
        assert not rig.history.heard

        await rig.advance(timedelta(seconds=10))

        assert (NAT.key, FAMILY_TG) in rig.history.heard
        await rig.runtime.stop()

    async def test_old_messages_are_pruned_at_start_and_every_day(self) -> None:
        store = InMemoryStore()
        clock = FakeClock()
        old = Message(
            MessageId("old"), FAMILY_TG, "t0", Author(ADA), "x", clock.now() - timedelta(days=8)
        )
        await store.add(old, [])
        rig = Rig(store=store, clock=clock)
        await rig.runtime.start()
        await settle()
        assert not store.messages

        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()
        rig.history.arrivals.append((clock.now(), fingerprint("x", "y")))
        await rig.advance(timedelta(days=8))

        assert not store.messages
        assert all(at >= clock.now() - timedelta(days=7) for at, _ in rig.history.arrivals)
        await rig.runtime.stop()

    async def test_a_failing_repository_is_logged_and_tried_again(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = Rig()
        await rig.runtime.start()
        rig.post(FAMILY_TG, NAT, "Привіт")
        await settle()

        async def broken(*args: object) -> Any:
            raise OSError("disk full")

        monkeypatch.setattr(rig.history, "record", broken)
        monkeypatch.setattr(rig.store, "prune", broken)
        await rig.advance(timedelta(days=1))

        assert "saving the routing history failed" in caplog.text
        assert "pruning old messages failed" in caplog.text
        await rig.runtime.stop()

    def test_the_periods_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            HubSettings(flush_every=timedelta(0))


class TestNewAccounts:
    async def test_the_admin_hears_of_an_account_seen_for_the_first_time(self) -> None:
        rig = Rig(edited(admin_notices={"to": "owner", "new_accounts": True}))
        await rig.runtime.start()

        rig.post(FAMILY_TG, ADA, "1")
        rig.post(FAMILY_TG, ADA, "2")
        rig.post(FAMILY_TG, NAT, "3")  # a person of the config
        await settle()

        [notice] = rig.notices()
        assert notice.startswith(
            "chatko: A new account at family.telegram: fake:ada (Ada Lovelace)."
        )
        assert set(rig.accounts.accounts) == {ADA.key, NAT.key}
        await rig.runtime.stop()

    async def test_without_new_accounts_in_the_config_nobody_is_told(self) -> None:
        rig = Rig()
        await rig.runtime.start()

        rig.post(FAMILY_TG, ADA, "1")
        await settle()

        assert rig.notices() == []
        assert ADA.key in rig.accounts.accounts
        await rig.runtime.stop()
