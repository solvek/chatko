"""MeshNode over the fake node: connecting, provisioning, ACKs, and what it tells the admin."""

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

import pytest
import pytest_asyncio

from chatko_meshtastic.api import (
    BROADCAST,
    AddContact,
    BeginEdit,
    CommitEdit,
    MeshConnection,
    NodeEntry,
    NodeInfo,
    NodeState,
    Packet,
    Routing,
    SetMqtt,
    SetOwner,
    Text,
    UnreachableError,
)
from chatko_meshtastic.config import MeshtasticConfig
from chatko_meshtastic.node import Ack, MeshNode, Nak, NodeTimings, NotReadyError
from chatko_meshtastic.provisioning import WantedNode, settings_commands
from chatko_meshtastic.testing import (
    HUB_NUM,
    FakeMeshApi,
    SentText,
    public_key_of,
    text_packet,
)

PRIVATE = bytes(range(32))
ADA_KEY = bytes(range(64, 96))
ADA = 0xA1B2C3D4
BOB = 0x0BADC0DE
CONFIG = MeshtasticConfig.model_validate(
    {
        "connection": {"tcp": "meshtasticd:4403"},
        "long_name": "chatko",
        "short_name": "CHKO",
        "region": "EU_868",
        "private_key": base64.b64encode(PRIVATE).decode(),
        "mqtt": {
            "host": "mosquitto",
            "username": "hub",
            "password": "pw",
            "root_topic": "msh/lab",
        },
        "channels": [
            {"name": "LongFast", "psk": "AQ=="},
            {"name": "Family", "psk": base64.b64encode(bytes(16)).decode()},
        ],
        "contacts": {"!a1b2c3d4": base64.b64encode(ADA_KEY).decode()},
    }
)
WANTED = WantedNode.from_config(CONFIG)
FAST = NodeTimings(
    min_send_interval=0.0,
    admin_timeout=0.2,
    reboot_timeout=0.3,
    reconnect=(0.005, 0.02),
    unreachable_notice=0.05,
    stable_connection=10.0,
    dropped_notice=3,
)


def provisioned(api: FakeMeshApi) -> FakeMeshApi:
    """The node with the settings and the contact of `CONFIG`."""
    settings = api.settings
    settings = replace(
        settings,
        long_name="chatko",
        short_name="CHKO",
        region="EU_868",
        ok_to_mqtt=True,
        ignore_mqtt=False,
        private_key=PRIVATE,
        public_key=public_key_of(PRIVATE),
        mqtt=WANTED.mqtt,
        channels=tuple(WANTED.channel(index) for index in range(8)),
    )
    api.settings = settings
    api.nodes[ADA] = NodeEntry(ADA, "Ada Lovelace", "ADA", ADA_KEY, favorite=True)
    return api


async def eventually(check: Callable[[], bool], within: float = 2.0) -> None:
    async with asyncio.timeout(within):
        while not check():  # noqa: ASYNC110 - the node's work shows only in the fake
            await asyncio.sleep(0.005)


class Rig:
    def __init__(
        self, api: FakeMeshApi | None = None, timings: NodeTimings = FAST, **hooks: Any
    ) -> None:
        self.api = api or FakeMeshApi()
        self.notices: list[tuple[str, str]] = []
        self.packets: list[Packet] = []
        self.readies = 0
        self.on_ready: Callable[[], None] | None = None
        self.node = MeshNode(
            self.api,
            WANTED,
            name="lab",
            logger=logging.getLogger("tests.mesh"),
            notify_admin=self._notify,
            on_packet=hooks.get("on_packet", self._packet),
            on_ready=self._ready,
            timings=timings,
        )

    async def _notify(self, text: str, key: str) -> None:
        self.notices.append((key, text))

    async def _packet(self, packet: Packet) -> None:
        self.packets.append(packet)

    async def _ready(self) -> None:
        self.readies += 1
        if self.on_ready is not None:
            self.on_ready()

    def notice_keys(self) -> list[str]:
        return [key for key, _ in self.notices]

    async def ready(self) -> None:
        await eventually(lambda: self.node.ready and self.readies > 0)


@pytest_asyncio.fixture
async def rig() -> AsyncIterator[Rig]:
    rig = Rig(provisioned(FakeMeshApi()))
    await rig.node.start()
    await rig.ready()
    yield rig
    await rig.node.stop()


@pytest_asyncio.fixture
async def fresh() -> AsyncIterator[Rig]:
    rig = Rig()
    await rig.node.start()
    yield rig
    await rig.node.stop()


# Provisioning.


async def test_provisions_a_fresh_node_and_serves_it_once_it_rebooted(fresh: Rig) -> None:
    await fresh.ready()

    api = fresh.api
    assert api.connections == 2  # the commit rebooted the node
    commands = api.settings_commands()
    assert commands[0] == BeginEdit()
    assert commands[-1] == CommitEdit()
    assert settings_commands(api.settings, WANTED) == []
    assert not api.settings.ignore_mqtt  # despite the firmware's first-region quirk
    assert api.admin[-1] == AddContact(ADA, ADA_KEY)
    assert api.nodes[ADA].favorite
    assert fresh.node.public_key == public_key_of(PRIVATE)
    assert fresh.node.num == HUB_NUM
    assert fresh.readies == 1
    assert fresh.notices == []


async def test_serves_a_provisioned_node_without_writing_or_rebooting(rig: Rig) -> None:
    assert rig.api.admin == []
    assert rig.api.connections == 1


async def test_sends_admin_messages_one_at_a_time(fresh: Rig) -> None:
    await fresh.ready()

    assert len(fresh.api.admin) > 5
    assert fresh.api.max_waiting_admin == 1


async def test_connects_again_when_an_admin_message_gets_no_answer(fresh: Rig) -> None:
    fresh.api.answer_admin = False
    await eventually(lambda: fresh.api.connections >= 2)
    assert not fresh.node.ready

    fresh.api.answer_admin = True
    await fresh.ready()


async def test_tells_the_admin_what_the_node_refuses_and_goes_on() -> None:
    rig = Rig()
    rig.api.refuse[SetMqtt] = "BAD_REQUEST"
    await rig.node.start()
    try:
        await rig.ready()
    finally:
        await rig.node.stop()

    assert rig.notice_keys() == ["refused:MQTT mosquitto as hub, root msh/lab", "provisioning"]
    assert "refused MQTT mosquitto as hub, root msh/lab: BAD_REQUEST" in rig.notices[0][1]
    assert CommitEdit() in rig.api.admin
    assert rig.api.connections == 2


async def test_tells_the_admin_once_when_a_setting_does_not_stick() -> None:
    rig = Rig()
    rig.api.ignore.add(SetOwner)
    await rig.node.start()
    try:
        await rig.ready()
        await asyncio.sleep(0.05)
    finally:
        await rig.node.stop()

    [(key, text)] = rig.notices
    assert key == "provisioning"
    assert "still differs from the config" in text
    assert "names 'chatko'/'CHKO'" in text.rpartition(":")[2]
    assert rig.api.connections == 2  # no reboot loop


async def test_provisions_again_what_changed_while_it_was_away(rig: Rig) -> None:
    rig.api.settings = replace(rig.api.settings, short_name="APP")
    rig.api.drop()

    await eventually(lambda: rig.readies == 2)
    assert rig.api.settings.short_name == "CHKO"
    assert rig.api.connections == 3
    assert rig.notices == []


async def test_a_refused_contact_is_told_and_not_taken() -> None:
    rig = Rig(provisioned(FakeMeshApi()))
    del rig.api.nodes[ADA]
    rig.api.refuse[AddContact] = "BAD_REQUEST"
    await rig.node.start()
    try:
        await rig.ready()
    finally:
        await rig.node.stop()

    assert rig.notice_keys() == ["refused:contact !a1b2c3d4"]
    assert rig.node.node(ADA) is None


async def test_matches_admin_answers_that_come_before_the_packet_id() -> None:
    rig = Rig()
    rig.api.answer_early = True
    await rig.node.start()
    try:
        await rig.ready()
    finally:
        await rig.node.stop()

    assert rig.api.connections == 2
    assert rig.notices == []


async def test_provisions_again_a_node_that_went_away_while_it_was_provisioned() -> None:
    rig = Rig()
    rig.api.answer_admin = False
    await rig.node.start()
    try:
        await eventually(lambda: len(rig.api.admin) == 1)
        rig.api.answer_admin = True
        rig.api.drop()  # the waiting admin message fails at once, not after its timeout
        await rig.ready()
    finally:
        await rig.node.stop()

    assert settings_commands(rig.api.settings, WANTED) == []


async def test_connects_again_when_the_node_does_not_reboot_after_a_commit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig()
    rig.api.reboot_on_commit = False
    await rig.node.start()
    try:
        await rig.ready()
    finally:
        await rig.node.stop()

    assert rig.api.connections == 2
    assert "node !c4a7b001 did not reboot after a commit" in caplog.messages


# Connecting.


async def test_tells_the_admin_once_when_the_node_stays_unreachable() -> None:
    rig = Rig(provisioned(FakeMeshApi()))
    rig.api.online = False
    await rig.node.start()
    try:
        await eventually(lambda: "node-unreachable" in rig.notice_keys())
        await asyncio.sleep(0.1)
        assert rig.notice_keys() == ["node-unreachable"]
        assert "fake node" in rig.notices[0][1]
        with pytest.raises(NotReadyError):
            await rig.node.send_text("hi")

        rig.api.online = True
        await rig.ready()
    finally:
        await rig.node.stop()


async def test_keeps_trying_after_an_unexpected_error_in_connecting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Flaky(FakeMeshApi):
        failed = False

        async def connect(self) -> MeshConnection:
            if not self.failed:
                self.failed = True
                raise RuntimeError("a bug")
            return await super().connect()

    rig = Rig(provisioned(Flaky()))
    await rig.node.start()
    try:
        await rig.ready()
    finally:
        await rig.node.stop()

    assert "cannot connect to the node at fake node" in caplog.messages


async def test_tells_the_admin_when_the_node_keeps_closing_the_connection(rig: Rig) -> None:
    rig.on_ready = rig.api.drop  # another client takes the node over and over
    rig.api.drop()

    await eventually(lambda: "node-dropped" in rig.notice_keys())
    assert "Meshtastic app or CLI" in rig.notices[0][1]


async def test_a_connection_that_held_long_enough_is_not_counted_as_dropped() -> None:
    rig = Rig(provisioned(FakeMeshApi()), timings=replace(FAST, stable_connection=0.0))
    await rig.node.start()
    try:
        await rig.ready()
        rig.on_ready = rig.api.drop
        rig.api.drop()
        await eventually(lambda: rig.readies >= 5)
    finally:
        await rig.node.stop()

    assert "node-dropped" not in rig.notice_keys()


async def test_is_ready_again_after_the_node_comes_back(rig: Rig) -> None:
    rig.api.online = False
    rig.api.drop()
    await eventually(lambda: not rig.node.ready)
    with pytest.raises(NotReadyError):
        await rig.node.send_text("hi")

    rig.api.online = True
    await eventually(lambda: rig.readies == 2)


async def test_logs_the_public_key_when_the_node_is_first_ready(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, "tests.mesh")
    rig = Rig(provisioned(FakeMeshApi()))
    await rig.node.start()
    try:
        await rig.ready()
        rig.api.drop()
        await eventually(lambda: rig.readies == 2)
    finally:
        await rig.node.stop()

    key = base64.b64encode(public_key_of(PRIVATE)).decode()
    assert f"node !c4a7b001 (chatko) is ready; its public key is {key}" in caplog.messages
    assert "node !c4a7b001 is ready again" in caplog.messages


async def test_keeps_serving_after_a_failure_of_its_own(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(FakeMeshApi):
        broken = True

        async def connect(self) -> MeshConnection:
            connection = await super().connect()
            if self.broken:
                self.broken = False
                return _BrokenEvents(connection)
            return connection

    rig = Rig(provisioned(Broken()))
    await rig.node.start()
    try:
        await rig.ready()
    finally:
        await rig.node.stop()

    assert rig.api.connections == 2
    assert any("lost the node" in message for message in caplog.messages)


class _BrokenEvents(MeshConnection):
    def __init__(self, inner: MeshConnection) -> None:
        self._inner = inner

    @property
    def state(self) -> NodeState:
        return self._inner.state

    def events(self) -> AsyncIterator[Packet]:
        raise RuntimeError("a bug")

    async def send_text(self, text: str, *, to: int, channel: int, want_ack: bool) -> int:
        raise NotImplementedError

    async def send_admin(self, command: Any) -> int:
        raise NotImplementedError

    async def close(self) -> None:
        await self._inner.close()


async def test_stops_without_leaving_tasks() -> None:
    before = asyncio.all_tasks()
    rig = Rig(provisioned(FakeMeshApi()))
    await rig.node.start()
    await rig.ready()

    await rig.node.stop()

    assert asyncio.all_tasks() - before == set()
    assert not rig.node.ready
    assert not rig.api.connected


async def test_stop_is_safe_without_start() -> None:
    rig = Rig()

    await rig.node.stop()

    assert rig.node.num is None
    assert rig.node.public_key is None


# Texts and their ACKs.


async def test_a_broadcast_is_delivered_by_the_implicit_ack(rig: Rig) -> None:
    sent = await rig.node.send_text("Привіт", channel=1)

    assert await sent.outcome(within=1) == Ack(HUB_NUM)
    assert rig.api.texts == [SentText(sent.packet_id, "Привіт", BROADCAST, 1, True)]


async def test_a_direct_message_is_delivered_only_by_the_destinations_ack(rig: Rig) -> None:
    rig.api.ack_texts = False
    sent = await rig.node.send_text("hi", to=ADA)

    rig.api.answer(sent.packet_id, by=HUB_NUM)  # the broker has it
    assert await sent.outcome(within=0.05) is None
    assert sent.reached_broker

    rig.api.answer(sent.packet_id, by=ADA)
    assert await sent.outcome(within=1) == Ack(ADA)


@pytest.mark.parametrize(
    ("by", "reason"),
    [(HUB_NUM, "MAX_RETRANSMIT"), (HUB_NUM, "PKI_SEND_FAIL_PUBLIC_KEY"), (ADA, "NO_CHANNEL")],
)
async def test_a_nak_ends_the_wait(rig: Rig, by: int, reason: str) -> None:
    rig.api.ack_texts = False
    sent = await rig.node.send_text("hi", to=ADA)

    rig.api.answer(sent.packet_id, by=by, error=reason)

    assert await sent.outcome(within=1) == Nak(by, reason)


async def test_matches_answers_that_come_before_the_packet_id(rig: Rig) -> None:
    rig.api.answer_early = True

    sent = await rig.node.send_text("hi", to=ADA)

    assert await sent.outcome(within=1) == Ack(ADA)
    assert sent.reached_broker


async def test_without_any_answer_the_node_dropped_the_text(rig: Rig) -> None:
    rig.api.ack_texts = False
    sent = await rig.node.send_text("hi", to=ADA)

    assert await sent.outcome(within=0.05) is None
    assert not sent.reached_broker


async def test_a_lost_connection_ends_the_wait(rig: Rig) -> None:
    rig.api.ack_texts = False
    sent = await rig.node.send_text("hi", to=ADA)

    rig.api.drop()

    assert await sent.outcome(within=1) is None


async def test_gives_up_waiting_for_old_texts(rig: Rig) -> None:
    rig.node._timings = replace(FAST, outgoing_ttl=0.0)
    rig.api.ack_texts = False
    first = await rig.node.send_text("one", to=ADA)
    await asyncio.sleep(0.01)

    await rig.node.send_text("two", to=ADA)

    assert first.done
    assert await first.outcome(within=0) is None


async def test_keeps_the_interval_between_texts() -> None:
    rig = Rig(provisioned(FakeMeshApi()), timings=replace(FAST, min_send_interval=0.1))
    await rig.node.start()
    loop = asyncio.get_running_loop()
    try:
        await rig.ready()
        await rig.node.send_text("one")
        started = loop.time()
        await rig.node.send_text("two")
        took = loop.time() - started
    finally:
        await rig.node.stop()

    assert took >= 0.09


async def test_the_ports_errors_reach_the_sender(rig: Rig) -> None:
    connection = rig.node._ready
    assert connection is not None
    await connection.close()

    with pytest.raises(UnreachableError):
        await rig.node.send_text("hi")


# Packets.


async def test_hands_over_packets_from_other_nodes_in_order_but_not_its_own(rig: Rig) -> None:
    rig.api.receive(text_packet(ADA, "one", packet_id=1, channel=1))
    rig.api.receive(Packet(HUB_NUM, ADA, 2, Routing()))
    rig.api.receive(text_packet(ADA, "two", packet_id=3, to=HUB_NUM))

    await eventually(lambda: len(rig.packets) == 2)
    assert [packet.payload for packet in rig.packets] == [Text("one"), Text("two")]
    assert rig.packets[1].is_direct
    assert rig.packets[1].pki


async def test_a_slow_handler_does_not_hold_up_acks() -> None:
    release = asyncio.Event()

    async def slow(packet: Packet) -> None:
        await release.wait()

    rig = Rig(provisioned(FakeMeshApi()), on_packet=slow)
    await rig.node.start()
    try:
        await rig.ready()
        rig.api.receive(text_packet(ADA, "hi", packet_id=1))
        sent = await rig.node.send_text("hi", to=ADA)
        assert await sent.outcome(within=1) == Ack(ADA)
    finally:
        release.set()
        await rig.node.stop()


async def test_a_failing_handler_is_logged_and_the_next_packet_handled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handled: list[Packet] = []

    async def fails_once(packet: Packet) -> None:
        handled.append(packet)
        if len(handled) == 1:
            raise RuntimeError("boom")

    rig = Rig(provisioned(FakeMeshApi()), on_packet=fails_once)
    await rig.node.start()
    try:
        await rig.ready()
        rig.api.receive(text_packet(ADA, "one", packet_id=1))
        rig.api.receive(text_packet(ADA, "two", packet_id=2))
        await eventually(lambda: len(handled) == 2)
    finally:
        await rig.node.stop()

    assert "could not handle what the node handed over" in caplog.messages


# The node database.


def node_info(sender: int, long_name: str, short_name: str, key: bytes = b"") -> Packet:
    return Packet(sender, BROADCAST, 7, NodeInfo(long_name, short_name, key))


async def test_knows_the_nodes_of_the_node_database(rig: Rig) -> None:
    assert rig.node.node(ADA) == NodeEntry(ADA, "Ada Lovelace", "ADA", ADA_KEY, favorite=True)
    assert rig.node.node(BOB) is None


async def test_takes_a_key_from_nodeinfo_like_the_node_does(rig: Rig) -> None:
    rig.api.receive(node_info(BOB, "Bob", "BOB"))
    await eventually(lambda: rig.node.node(BOB) is not None)
    assert rig.node.node(BOB) == NodeEntry(BOB, "Bob", "BOB")

    rig.api.receive(node_info(BOB, "Bob B", "BOB", b"bob key"))  # the first key is learned
    await eventually(lambda: rig.node.node(BOB) == NodeEntry(BOB, "Bob B", "BOB", b"bob key"))

    rig.api.receive(node_info(BOB, "Mallory", "MAL", b"other key"))  # pinned: dropped
    rig.api.receive(node_info(BOB, "Mallory", "MAL"))  # no key while one is pinned: dropped
    rig.api.receive(node_info(BOB, "Robert", "BOB", b"bob key"))  # same key: names updated
    await eventually(lambda: len(rig.packets) == 5)
    assert rig.node.node(BOB) == NodeEntry(BOB, "Robert", "BOB", b"bob key")


async def test_a_favorite_without_a_key_stays_a_favorite(rig: Rig) -> None:
    rig.node._nodes[BOB] = NodeEntry(BOB, favorite=True)

    rig.api.receive(node_info(BOB, "Bob", "BOB", b"bob key"))

    await eventually(lambda: rig.packets != [])
    assert rig.node.node(BOB) == NodeEntry(BOB, "Bob", "BOB", b"bob key", favorite=True)
