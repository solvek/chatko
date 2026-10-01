"""A hub's application layer wired with fakes: two `FakeExtension` instances on fake networks.

Topology:
- group `family`: `family.tg` (instance `tg`, place `family`), `family.radio` (instance `mesh`,
  place `family-dm`, recipients `!a1` and `!b2`), `family.channel` (`mesh`, `family-ch`);
- group `street`: `street.tg` (`tg`, `street`), `street.channel` (`mesh`, `street-ch`);
- sources `longfast` (`mesh`, `longfast`) and `owner` (`tg`, `owner`);
- person `NatAda` with the account `fake:nat`.
"""

import asyncio
from collections.abc import Mapping
from datetime import timedelta

from chatko.application.history import HubHistory
from chatko.application.hub import ExtensionHub
from chatko.application.installation import Installation
from chatko.application.outbox import OutboxSettings, OutboxWorker
from chatko.application.pipeline import InboundPipeline
from chatko.application.routing import DefaultRouter, Router
from chatko.application.testing import FakeClock, InMemoryStore, RecordingNotices, SequentialIds
from chatko.domain import Account, AccountKey, EndpointRef, Group, Person, Topology
from chatko.extension_api.testing import FakeConfig, FakeEndpointConfig, FakeExtension, FakeNetwork

FAMILY_TG = EndpointRef("tg", "family.tg")
FAMILY_RADIO = EndpointRef("mesh", "family.radio")
FAMILY_CHANNEL = EndpointRef("mesh", "family.channel")
STREET_TG = EndpointRef("tg", "street.tg")
STREET_CHANNEL = EndpointRef("mesh", "street.channel")
LONGFAST = EndpointRef("mesh", "longfast")
OWNER = EndpointRef("tg", "owner")
RECIPIENTS = ("!a1", "!b2")

NAT = Account(AccountKey("fake", "nat"), "Наталія Адамчук")
ADA = Account(AccountKey("fake", "ada"), "Ada Lovelace")
NATADA = Person("NatAda", frozenset({NAT.key}))

TOPOLOGY = Topology(
    groups=(
        Group("family", (FAMILY_TG, FAMILY_RADIO, FAMILY_CHANNEL)),
        Group("street", (STREET_TG, STREET_CHANNEL)),
    ),
    sources={"longfast": LONGFAST, "owner": OWNER},
    people=(NATADA,),
)

PLACES: Mapping[EndpointRef, FakeEndpointConfig] = {
    FAMILY_TG: FakeEndpointConfig(place="family"),
    FAMILY_RADIO: FakeEndpointConfig(place="family-dm", recipients=RECIPIENTS),
    FAMILY_CHANNEL: FakeEndpointConfig(place="family-ch"),
    STREET_TG: FakeEndpointConfig(place="street"),
    STREET_CHANNEL: FakeEndpointConfig(place="street-ch"),
    LONGFAST: FakeEndpointConfig(place="longfast"),
    OWNER: FakeEndpointConfig(place="owner"),
}


async def settle(rounds: int = 50) -> None:
    """Let every task run until it waits for something other than the event loop."""
    for _ in range(rounds):
        await asyncio.sleep(0)


class Rig:
    """The pipeline, the outbox worker and a hub per instance, over in-memory fakes."""

    def __init__(
        self,
        *,
        router: Router | None = None,
        fingerprint_dedup: Mapping[EndpointRef, timedelta] | None = None,
        settings: OutboxSettings | None = None,
        store: InMemoryStore | None = None,
        clock: FakeClock | None = None,
        networks: Mapping[str, FakeNetwork] | None = None,
    ) -> None:
        self.clock = FakeClock() if clock is None else clock
        self.store = InMemoryStore() if store is None else store
        self.history = HubHistory()
        self.notices = RecordingNotices()
        self.networks = (
            {"tg": FakeNetwork(), "mesh": FakeNetwork()} if networks is None else dict(networks)
        )
        self.worker = OutboxWorker(
            installation=self.current,
            messages=self.store,
            outbox=self.store,
            clock=self.clock,
            settings=settings,
        )
        self.pipeline = InboundPipeline(
            installation=self.current,
            messages=self.store,
            outbox=self.worker,
            router=DefaultRouter() if router is None else router,
            history=self.history,
            clock=self.clock,
            ids=SequentialIds(),
        )
        self.extensions = {
            instance: FakeExtension(
                instance, FakeConfig(), self.hub(instance), network=self.networks[instance]
            )
            for instance in self.networks
        }
        for instance, extension in self.extensions.items():
            extension.set_endpoints(
                {ref: config for ref, config in PLACES.items() if ref.instance == instance}
            )
        self.installation = Installation(TOPOLOGY, self.extensions, fingerprint_dedup or {})

    def current(self) -> Installation:
        return self.installation

    def hub(self, instance: str) -> ExtensionHub:
        return ExtensionHub(
            instance,
            pipeline=self.pipeline,
            outbox=self.worker,
            history=self.history,
            notices=self.notices,
            clock=self.clock,
        )

    async def start(self) -> None:
        await self.worker.start()
        for extension in self.extensions.values():
            await extension.start()

    async def stop(self) -> None:
        for extension in self.extensions.values():
            await extension.stop()
        await self.worker.stop()

    async def advance_past_retries(self, rounds: int = 3) -> None:
        """Move the clock past the first few retries, letting the worker run after each."""
        for _ in range(rounds):
            self.clock.advance(timedelta(hours=1))
            await settle()

    def posted(self, endpoint: EndpointRef, *, recipient: str | None = None) -> list[str]:
        """What the hub posted at `endpoint` (to `recipient`), oldest first."""
        extension = self.extensions[endpoint.instance]
        return [
            post.text
            for post in extension.network.posts
            if post.place == PLACES[endpoint].place
            and post.author.key == extension.account.key
            and post.recipient == recipient
        ]
