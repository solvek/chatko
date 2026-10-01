"""An in-memory network and an extension for it.

`FakeExtension` is the reference extension: the contract suite checks itself against it, and the
core's tests use it in place of real networks.
"""

import asyncio
import itertools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chatko.domain import Account, AccountKey, Attachment, EndpointRef
from chatko.extension_api.delivery import (
    Delivered,
    DeliveryReport,
    DeliveryResult,
    Failed,
    Retry,
)
from chatko.extension_api.endpoints import EndpointProvider
from chatko.extension_api.extension import Extension
from chatko.extension_api.hub import HubContext
from chatko.extension_api.messages import InboundMessage, OutboundMessage

type Subscriber = Callable[[FakePost], None]


@dataclass(frozen=True, slots=True)
class FakePost:
    """A post at a place. At a place with recipients, `recipient` is the one the post is between
    the hub and: the addressee of the hub's post, or the sender of anyone else's."""

    id: str
    place: str
    author: Account
    text: str
    attachments: tuple[Attachment, ...] = ()
    recipient: str | None = None


class FakeNetwork:
    """Places where accounts post. Every subscriber is handed every post as it is made."""

    def __init__(self) -> None:
        self.posts: list[FakePost] = []
        self.online = True
        self._subscribers: list[Subscriber] = []
        self._ids = itertools.count(1)

    def post(
        self,
        place: str,
        author: Account,
        text: str,
        *,
        attachments: Iterable[Attachment] = (),
        recipient: str | None = None,
    ) -> FakePost:
        post = FakePost(f"p{next(self._ids)}", place, author, text, tuple(attachments), recipient)
        self.posts.append(post)
        self.hand_over(post)
        return post

    def hand_over(self, post: FakePost) -> None:
        """Hand a post to every subscriber; again, for one that was handed over before."""
        for subscriber in list(self._subscribers):
            subscriber(post)

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def texts(self, place: str, *, by: AccountKey | None = None) -> list[str]:
        """The texts posted at `place`, by `by` only if it is given, oldest first."""
        return [
            post.text
            for post in self.posts
            if post.place == place and (by is None or post.author.key == by)
        ]


class FakeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: str = "hub"
    """The hub's own account in the network."""
    max_text: int | None = Field(default=None, gt=1)
    """Posts longer than this are cut, as on a network with small packets."""


class FakeEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    place: str = Field(min_length=1)
    recipients: tuple[str, ...] = ()


class FakeExtension(Extension[FakeConfig], EndpointProvider[FakeEndpointConfig]):
    """An extension for a `FakeNetwork`. It also records the delivery reports it gets."""

    type_name: ClassVar[str] = "fake"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = FakeConfig
    endpoint_config_model: ClassVar[type[BaseModel]] = FakeEndpointConfig

    def __init__(
        self,
        instance: str,
        config: FakeConfig,
        hub: HubContext,
        *,
        network: FakeNetwork | None = None,
    ) -> None:
        super().__init__(instance, config, hub)
        self.network = FakeNetwork() if network is None else network
        self.account = Account(AccountKey(self.type_name, config.account), "chatko")
        self.reports: list[DeliveryReport] = []
        self._endpoints: dict[EndpointRef, FakeEndpointConfig] = {}
        self._by_place: dict[str, EndpointRef] = {}
        self._inbox: asyncio.Queue[FakePost] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None

    def set_endpoints(self, endpoints: Mapping[EndpointRef, FakeEndpointConfig]) -> None:
        by_place: dict[str, EndpointRef] = {}
        for endpoint, config in endpoints.items():
            if config.place in by_place:
                raise ValueError(
                    f"{endpoint} and {by_place[config.place]} are the same place {config.place!r}"
                )
            by_place[config.place] = endpoint
        self._endpoints = dict(endpoints)
        self._by_place = by_place

    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]:
        config = self._endpoints.get(endpoint)
        return () if config is None else config.recipients

    async def start(self) -> None:
        self.network.subscribe(self._inbox.put_nowait)
        self._reader = asyncio.create_task(self._read(), name=f"chatko.fake.{self.instance}")

    async def stop(self) -> None:
        self.network.unsubscribe(self._inbox.put_nowait)
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.wait({self._reader})
            self._reader = None

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        config = self._endpoints.get(endpoint)
        if config is None:
            return Failed(f"{endpoint} is not an endpoint of {self.instance}")
        if message.recipient not in (config.recipients or (None,)):
            return Failed(f"{message.recipient!r} is not a recipient of {endpoint}")
        if not self.network.online:
            return Retry("the network is offline")
        text = message.formatted
        limit = self.config.max_text
        truncated = False
        if limit is not None and len(text) > limit:
            text, truncated = text[: limit - 1] + "…", True
        self.network.post(config.place, self.account, text, recipient=message.recipient)
        return Delivered(truncated=truncated)

    async def delivery_report(self, report: DeliveryReport) -> None:
        self.reports.append(report)

    async def _read(self) -> None:
        while True:
            post = await self._inbox.get()
            endpoint = self._by_place.get(post.place)
            if endpoint is None or post.author.key == self.account.key:
                continue
            recipients = self._endpoints[endpoint].recipients
            if recipients and post.recipient not in recipients:
                continue  # like a direct message from a node in no `dm` list
            from_recipient = post.recipient if recipients else None
            await self.hub.submit(
                InboundMessage(
                    endpoint, post.id, post.author, post.text, post.attachments, from_recipient
                )
            )
