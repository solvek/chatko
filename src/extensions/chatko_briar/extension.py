"""The Briar extension: private groups of the hub's `briar-headless` account as endpoints
(design.md §7)."""

import asyncio
from collections.abc import Collection, Mapping
from typing import ClassVar

from pydantic import BaseModel

from chatko.extension_api import (
    Account,
    AccountKey,
    Delivered,
    DeliveryResult,
    EndpointProvider,
    EndpointRef,
    Extension,
    Failed,
    HubContext,
    InboundMessage,
    OutboundMessage,
    Retry,
)
from chatko_briar import ids
from chatko_briar.api import (
    BriarApi,
    BriarError,
    DissolvedError,
    Event,
    GroupDissolved,
    GroupMessage,
    GroupUnavailableError,
    MessageAdded,
    MessageKind,
    RefusedError,
    RejectedError,
)
from chatko_briar.config import BriarConfig, BriarGroup
from chatko_briar.http_api import HttpBriarApi

MAX_POST = 31_744
"""The longest post, in bytes of UTF-8."""

REMEMBERED_POSTS = 4096
"""How many handed-over posts the extension remembers."""

RECONNECT_BACKOFF = (1.0, 60.0)
"""Seconds to wait after a failed connection: the first wait, doubling up to the longest."""


class BriarExtension(Extension[BriarConfig], EndpointProvider[BriarGroup]):
    """Reads and posts in the private groups of its endpoints, as the hub's Briar account.

    It manages no contacts, groups or invitations: that is `briarctl` (design.md §7.1, D24). After
    every (re)connection of the WebSocket it lists the messages of each group and submits the
    posts that are still unread, and it marks a post read once the hub has stored it, so nothing
    posted while chatko was down is lost (design.md §7.2).

    `api` replaces `briar-headless` (tests); without it, `start` connects over HTTP.
    """

    type_name: ClassVar[str] = "briar"
    api_version: ClassVar[tuple[int, int]] = (1, 0)
    config_model: ClassVar[type[BaseModel]] = BriarConfig
    endpoint_config_model: ClassVar[type[BaseModel]] = BriarGroup

    def __init__(
        self,
        instance: str,
        config: BriarConfig,
        hub: HubContext,
        *,
        api: BriarApi | None = None,
        backoff: tuple[float, float] = RECONNECT_BACKOFF,
    ) -> None:
        super().__init__(instance, config, hub)
        self._given_api = api
        self._backoff = backoff
        self._api: BriarApi | None = None
        self._runner: asyncio.Task[None] | None = None
        self._catch_ups: set[asyncio.Task[None]] = set()
        self._connected: BriarApi | None = None
        """The API while the WebSocket is open and the catch-up after it is done."""
        self._groups: dict[EndpointRef, bytes] = {}
        self._by_group: dict[bytes, EndpointRef] = {}
        self._dissolved: set[bytes] = set()
        self._handed_over: dict[bytes, None] = {}
        """The posts handed to the hub lately, oldest first, so that the overlap of a catch-up and
        the WebSocket costs the hub no second look (it would drop the copy by the id anyway)."""

    # Endpoints.

    def set_endpoints(self, endpoints: Mapping[EndpointRef, BriarGroup]) -> None:
        groups: dict[EndpointRef, bytes] = {}
        owners: dict[bytes, EndpointRef] = {}
        for endpoint, config in endpoints.items():
            if config.group_id in owners:
                raise ValueError(f"{endpoint} and {owners[config.group_id]} are the same group")
            owners[config.group_id] = endpoint
            groups[endpoint] = config.group_id
        added = [group for group in owners if group not in self._by_group]
        self._groups = groups
        self._by_group = owners
        if self._connected is not None and added:
            self._start_catch_up(self._connected, added)

    # Lifecycle.

    async def start(self) -> None:
        if self._given_api is not None:
            self._api = self._given_api
        else:
            self._api = HttpBriarApi(
                self.config.api, self.config.auth_token.get_secret_value(), self.logger
            )
        self._runner = asyncio.create_task(
            self._run(self._api), name=f"chatko.briar.{self.instance}"
        )
        self.logger.info("connecting to briar-headless at %s", self.config.api)

    async def stop(self) -> None:
        tasks = [*self._catch_ups]
        if self._runner is not None:
            tasks.append(self._runner)
            self._runner = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        if self._api is not None:
            await self._api.close()
            self._api = None

    # Reading.

    async def _run(self, api: BriarApi) -> None:
        failures = 0
        while True:
            try:
                stream = await api.subscribe()
                try:
                    await self._catch_up(api, list(self._by_group))
                    self._connected = api
                    self.logger.info("connected to briar-headless")
                    failures = 0
                    while True:
                        await self._handle(api, await stream.next())
                finally:
                    self._connected = None
                    await stream.close()
            except RefusedError as error:
                failures += 1
                self.logger.warning("briar-headless refused the hub: %s", error.reason)
                await self._refused(error)
            except BriarError as error:
                failures += 1
                self.logger.warning("lost briar-headless: %s", error.reason)
            except Exception:
                # The hub could not take a post: it stays unread, so the catch-up after the
                # reconnection hands it over again.
                failures += 1
                self.logger.exception("could not hand over a Briar message")
            await asyncio.sleep(self._wait(failures))

    def _wait(self, failures: int) -> float:
        first, longest = self._backoff
        return min(first * 2.0 ** min(failures - 1, 32), longest)

    def _start_catch_up(self, api: BriarApi, groups: Collection[bytes]) -> None:
        task = asyncio.create_task(
            self._catch_up_in_background(api, groups), name=f"chatko.briar.{self.instance}.catch-up"
        )
        self._catch_ups.add(task)
        task.add_done_callback(self._catch_ups.discard)

    async def _catch_up_in_background(self, api: BriarApi, groups: Collection[bytes]) -> None:
        try:
            await self._catch_up(api, groups)
        except BriarError as error:
            self.logger.warning("could not catch up with the new Briar groups: %s", error.reason)
        except Exception:
            self.logger.exception("could not catch up with the new Briar groups")

    async def _catch_up(self, api: BriarApi, groups: Collection[bytes]) -> None:
        """Submit the unread posts of the groups, oldest first."""
        member = {group.id: group for group in await api.groups()}
        for group_id in groups:
            endpoint = self._by_group.get(group_id)
            if endpoint is None:
                continue
            group = member.get(group_id)
            if group is None:
                await self._not_a_member(endpoint)
                continue
            if group.dissolved:
                await self._on_dissolved(group_id)
            try:
                posts = await api.messages(group_id)
            except GroupUnavailableError:
                await self._not_a_member(endpoint)
                continue
            for post in posts:
                await self._on_message(api, post)

    async def _handle(self, api: BriarApi, event: Event) -> None:
        match event:
            case MessageAdded(message):
                await self._on_message(api, message)
            case GroupDissolved(group_id):
                await self._on_dissolved(group_id)

    async def _on_message(self, api: BriarApi, message: GroupMessage) -> None:
        endpoint = self._by_group.get(message.group_id)
        if endpoint is None or message.own or message.kind is not MessageKind.POST or message.read:
            return
        if message.id in self._handed_over:
            return
        if message.text.strip():
            author = Account(
                AccountKey(self.type_name, ids.to_text(message.author_id)),
                message.author_name.strip(),
            )
            self._remember(message.id)
            try:
                await self.hub.submit(
                    InboundMessage(endpoint, ids.to_text(message.id), author, message.text)
                )
            except BaseException:
                del self._handed_over[message.id]
                raise
        try:
            await api.mark_read(message.group_id, message.id)
        except BriarError as error:
            # Still unread, so the next catch-up submits it again and the hub drops the copy.
            self.logger.info("could not mark a post of %s read: %s", endpoint, error.reason)

    def _remember(self, message_id: bytes) -> None:
        self._handed_over[message_id] = None
        if len(self._handed_over) > REMEMBERED_POSTS:
            del self._handed_over[next(iter(self._handed_over))]

    async def _on_dissolved(self, group_id: bytes) -> None:
        endpoint = self._by_group.get(group_id)
        if endpoint is None or group_id in self._dissolved:
            return
        self._dissolved.add(group_id)
        await self.hub.notify_admin(
            f"{endpoint}: the creator dissolved the Briar group {ids.to_text(group_id)}, so the "
            "hub cannot post there any more. Make a new group, join it with `briarctl` and "
            "put its id in the config.",
            key=f"dissolved:{ids.to_text(group_id)}",
        )

    async def _not_a_member(self, endpoint: EndpointRef) -> None:
        await self.hub.notify_admin(
            f"{endpoint}: the hub is not a member of the Briar group "
            f"{ids.to_text(self._groups[endpoint])}. Accept its invitation with "
            "`briarctl invitation accept`, or correct the group id in the config. Messages for "
            "it wait until then.",
            key=f"not-a-member:{endpoint.name}",
        )

    # Delivering.

    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult:
        group_id = self._groups.get(endpoint)
        if group_id is None:
            return Failed(f"{endpoint} is not an endpoint of {self.instance}")
        if message.recipient is not None:
            return Failed(f"{endpoint} has no recipients, so not {message.recipient!r}")
        if group_id in self._dissolved:
            return Failed(f"the creator dissolved the Briar group of {endpoint}")
        api = self._api
        if api is None:
            return Retry(f"{self.instance} is not running")
        text, truncated = _fit(message.formatted)
        try:
            await api.post(group_id, text)
        except BriarError as error:
            return await self._not_posted(endpoint, group_id, error)
        return Delivered(truncated=truncated)

    async def _not_posted(
        self, endpoint: EndpointRef, group_id: bytes, error: BriarError
    ) -> DeliveryResult:
        match error:
            case DissolvedError():
                await self._on_dissolved(group_id)
                return Failed(f"the creator dissolved the Briar group of {endpoint}")
            case GroupUnavailableError():
                await self._not_a_member(endpoint)
            case RefusedError():
                await self._refused(error)
            case RejectedError():
                return Failed(error.reason)
        return Retry(error.reason)

    async def _refused(self, error: RefusedError) -> None:
        await self.hub.notify_admin(
            f"briar-headless refuses {self.instance}: {error.reason}. Check `auth_token` in the "
            "config.",
            key="refused",
        )


def _fit(text: str) -> tuple[str, bool]:
    """The text cut to `MAX_POST` bytes with `…` at the end, and whether it was cut."""
    encoded = text.encode()
    if len(encoded) <= MAX_POST:
        return text, False
    # Half a character at the cut is dropped.
    return encoded[: MAX_POST - len("…".encode())].decode(errors="ignore") + "…", True
