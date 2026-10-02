"""The whole hub end to end: `chatko run`'s composition root with real files, real SQLite and
the system clock, and two `FakeExtension` instances in place of real networks."""

import asyncio
import os
import signal
import sqlite3
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from chatko.app.cli import main
from chatko.app.run import DATABASE_FILE, run_hub, serve
from chatko.domain import Account, AccountKey, EndpointRef
from chatko.extension_api import HubContext
from chatko.extension_api.testing import FakeConfig, FakeEndpointConfig, FakeExtension, FakeNetwork

NAT = Account(AccountKey("fake", "nat"), "Наталія Адамчук")
HUB = AccountKey("fake", "hub")

CONFIG = """
extensions:
  telegram: { type: fake }
  mesh: { type: fake, account: "${MESH_ACCOUNT}" }
groups:
  family:
    sites:
      telegram: { ext: telegram, place: family }
      channel: { ext: mesh, place: family-ch }
sources:
  owner: { ext: telegram, place: owner }
admin_notices: { to: owner }
"""

TO_OWNER = """
from chatko.routing_api import to_endpoint

def route(msg, ctx):
    return [to_endpoint(ctx.source("owner"), text="via script: " + msg.text)]
"""

ENV = {"MESH_ACCOUNT": "hub"}


@dataclass
class World:
    """The fake networks, which outlive the hub, and what the extensions did on them."""

    networks: dict[str, FakeNetwork] = field(default_factory=dict)
    started: dict[str, asyncio.Event] = field(default_factory=dict)
    endpoints: dict[str, list[EndpointRef]] = field(default_factory=dict)
    stopped: set[str] = field(default_factory=set)

    def types(self) -> dict[str, type[FakeExtension]]:
        world = self

        class Networked(FakeExtension):
            def __init__(self, instance: str, config: FakeConfig, hub: HubContext) -> None:
                network = world.networks.setdefault(instance, FakeNetwork())
                super().__init__(instance, config, hub, network=network)

            def set_endpoints(self, endpoints: Mapping[EndpointRef, FakeEndpointConfig]) -> None:
                super().set_endpoints(endpoints)
                world.endpoints[self.instance] = list(endpoints)

            async def start(self) -> None:
                await super().start()
                world.started.setdefault(self.instance, asyncio.Event()).set()

            async def stop(self) -> None:
                await super().stop()
                world.stopped.add(self.instance)

        return {"fake": Networked}

    def post(self, instance: str, place: str, text: str) -> None:
        self.networks[instance].post(place, NAT, text)

    def posted(self, instance: str, place: str) -> list[str]:
        network = self.networks.get(instance)
        posts = [] if network is None else network.posts
        return [post.text for post in posts if post.place == place and post.author.key == HUB]

    async def until(self, condition: Callable[[], bool], seconds: float = 10) -> None:
        async with asyncio.timeout(seconds):
            while not condition():  # noqa: ASYNC110 - the hub's work shows only in the fakes
                await asyncio.sleep(0.02)

    async def write_until(self, path: Path, text: str, condition: Callable[[], bool]) -> None:
        """Write the file until the hub has acted on it: a write right after the start may come
        before the watcher watches."""
        for _ in range(20):
            path.write_text(text, encoding="utf-8")
            try:
                await self.until(condition, seconds=1)
            except TimeoutError:
                continue
            return
        pytest.fail(f"the hub did not act on {path}")

    async def until_started(self, *instances: str) -> None:
        for instance in instances:
            event = self.started.setdefault(instance, asyncio.Event())
            await asyncio.wait_for(event.wait(), 10)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "chatko.yaml"
    path.parent.mkdir()
    path.write_text(CONFIG, encoding="utf-8")
    return path


@asynccontextmanager
async def running(world: World, config: Path, data: Path) -> AsyncIterator[asyncio.Task[int]]:
    """The hub runs inside the block and is stopped at its end."""
    stop = asyncio.Event()
    hub = asyncio.create_task(run_hub(config, ENV, world.types(), data=data, stop=stop))
    try:
        yield hub
    finally:
        stop.set()
        assert await asyncio.wait_for(hub, 15) == 0
        world.started.clear()


def stored_texts(data: Path) -> list[str]:
    with sqlite3.connect(data / DATABASE_FILE) as db:
        return [row[0] for row in db.execute("SELECT text FROM messages ORDER BY received_at")]


def delivery_states(data: Path) -> list[str]:
    with sqlite3.connect(data / DATABASE_FILE) as db:
        return [row[0] for row in db.execute("SELECT state FROM deliveries ORDER BY seq")]


async def test_a_message_crosses_from_one_site_to_the_other_through_sqlite(
    config: Path, tmp_path: Path
) -> None:
    world, data = World(), tmp_path / "data"
    async with running(world, config, data):
        await world.until_started("telegram", "mesh")

        world.post("telegram", "family", "Привіт")
        await world.until(lambda: world.posted("mesh", "family-ch") == ["NatAda: Привіт"])

    assert stored_texts(data) == ["Привіт"]
    assert delivery_states(data) == ["delivered"]


async def test_messages_wait_in_sqlite_across_a_restart(config: Path, tmp_path: Path) -> None:
    world, data = World(), tmp_path / "data"
    async with running(world, config, data):
        await world.until_started("telegram", "mesh")
        world.networks["mesh"].online = False
        world.post("telegram", "family", "1")
        await world.until(lambda: stored_texts(data) == ["1"])

    world.networks["mesh"].online = True
    async with running(world, config, data):
        await world.until(lambda: world.posted("mesh", "family-ch") == ["NatAda: 1"])

    assert delivery_states(data) == ["delivered"]


async def test_a_changed_config_and_a_new_routing_script_apply_while_it_runs(
    config: Path, tmp_path: Path
) -> None:
    world, data = World(), tmp_path / "data"
    async with running(world, config, data):
        await world.until_started("telegram", "mesh")

        with_radio = CONFIG.replace(
            "      channel: { ext: mesh, place: family-ch }\n",
            "      channel: { ext: mesh, place: family-ch }\n"
            "      radio: { ext: mesh, place: family-radio }\n",
        )
        await world.write_until(config, with_radio, lambda: len(world.endpoints["mesh"]) == 2)
        world.post("telegram", "family", "1")
        await world.until(lambda: world.posted("mesh", "family-radio") == ["NatAda: 1"])

        (config.parent / "routing.py").write_text(TO_OWNER, encoding="utf-8")
        await world.write_until(
            config, CONFIG + "routing: routing.py\n", lambda: len(world.endpoints["mesh"]) == 1
        )
        world.post("telegram", "family", "2")
        await world.until(lambda: world.posted("telegram", "owner") == ["NatAda: via script: 2"])


async def test_without_a_valid_config_it_does_not_start(config: Path, tmp_path: Path) -> None:
    config.write_text("extensions: { telegram: { type: nope } }\n", encoding="utf-8")

    code = await run_hub(config, ENV, World().types(), data=tmp_path, stop=asyncio.Event())

    assert code == 1


def test_chatko_run_exits_with_1_without_a_valid_config(config: Path, tmp_path: Path) -> None:
    config.write_text("extensions: { telegram: { type: nope } }\n", encoding="utf-8")

    code = main(
        ["run", "--config", str(config), "--data", str(tmp_path / "data"), "--env-file", "-"],
        extensions=World().types(),
    )

    assert code == 1


@pytest.mark.skipif(sys.platform == "win32", reason="no signal handlers in the Windows loop")
async def test_sigterm_stops_the_hub_in_order(config: Path, tmp_path: Path) -> None:
    world = World()
    hub = asyncio.create_task(serve(config, ENV, world.types(), data=tmp_path / "data"))
    await world.until_started("telegram", "mesh")

    os.kill(os.getpid(), signal.SIGTERM)

    assert await asyncio.wait_for(hub, 15) == 0


async def test_without_signal_handlers_a_cancelled_run_still_stops_in_order(
    config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unsupported(*args: object) -> None:
        raise NotImplementedError  # as in the Windows event loop

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", unsupported)
    world = World()
    hub = asyncio.create_task(serve(config, ENV, world.types(), data=tmp_path / "data"))
    await world.until_started("telegram", "mesh")

    hub.cancel()  # what `asyncio.run` does on Ctrl+C there

    await asyncio.wait({hub}, timeout=15)
    assert hub.cancelled()
    assert world.stopped == {"telegram", "mesh"}
