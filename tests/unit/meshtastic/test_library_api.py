"""The adapter over the meshtastic library, against a node at the other end of a socket pair."""

import asyncio
import socket
import threading
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from meshtastic.protobuf import admin_pb2, channel_pb2, config_pb2, mesh_pb2, portnums_pb2

from chatko_meshtastic.api import (
    BROADCAST,
    AddContact,
    AdminCommand,
    BeginEdit,
    ChannelRole,
    ChannelSettings,
    CommitEdit,
    MeshConnection,
    MqttSettings,
    NodeEntry,
    NodeInfo,
    Other,
    Packet,
    RejectedError,
    Routing,
    SetChannel,
    SetLora,
    SetMqtt,
    SetOwner,
    SetPrivateKey,
    Text,
    UnreachableError,
)
from chatko_meshtastic.library_api import MAX_PAYLOAD, LibraryMeshApi
from tests.unit.meshtastic.device import HUB, FakeDevice, admin_of, mesh_packet, node_info

ADA = 0xA1B2C3D4
PORT = portnums_pb2.PortNum


@pytest.fixture
def device() -> FakeDevice:
    device = FakeDevice()
    device.nodes.append(node_info(ADA, "Ada Lovelace", "ADA", b"ada key"))
    device.nodes[-1].is_favorite = True
    return device


def api_for(device: FakeDevice, timeout: float = 2.0) -> LibraryMeshApi:
    return LibraryMeshApi("node", 4403, open_socket=device.open_socket, timeout=timeout)


@pytest_asyncio.fixture
async def connection(device: FakeDevice) -> AsyncIterator[MeshConnection]:
    connection = await api_for(device).connect()
    yield connection
    await connection.close()


async def next_packet(connection: MeshConnection) -> Packet:
    async with asyncio.timeout(2):
        async for packet in connection.events():
            return packet
    raise AssertionError("the connection ended")


async def end_of(connection: MeshConnection) -> list[Packet]:
    async with asyncio.timeout(2):
        return [packet async for packet in connection.events()]


# Connecting.


async def test_reads_the_node_settings_and_database_on_connect(connection: MeshConnection) -> None:
    state = connection.state
    settings = state.settings

    assert state.num == HUB
    assert (settings.long_name, settings.short_name) == ("chatko hub", "HUB")
    assert (settings.region, settings.ok_to_mqtt, settings.ignore_mqtt) == ("EU_868", True, False)
    assert settings.private_key == b"hub private key"
    assert settings.public_key == b"hub public key"
    assert settings.mqtt == MqttSettings(
        enabled=True,
        address="mosquitto",
        username="hub",
        password="pw",
        root="msh/lab",
        encryption=True,
    )
    assert settings.channels == (
        ChannelSettings(0, ChannelRole.PRIMARY, "", b"\x01", True, True),
        ChannelSettings(1, ChannelRole.SECONDARY, "Family", bytes(16), False, False),
        *(ChannelSettings(index) for index in range(2, 8)),
    )
    assert state.nodes == (NodeEntry(ADA, "Ada Lovelace", "ADA", b"ada key", favorite=True),)


async def test_a_fresh_node_has_no_region() -> None:
    device = FakeDevice()
    device.lora = config_pb2.Config.LoRaConfig()

    connection = await api_for(device).connect()
    await connection.close()

    assert connection.state.settings.region == "UNSET"


async def test_names_the_node_by_its_address(device: FakeDevice) -> None:
    assert api_for(device).address == "tcp node:4403"


async def test_cannot_connect_to_a_node_that_is_down() -> None:
    def refused() -> socket.socket:
        raise ConnectionRefusedError("refused")

    api = LibraryMeshApi("node", 4403, open_socket=refused)

    with pytest.raises(UnreachableError, match="cannot connect to the node at tcp node:4403"):
        await api.connect()


async def test_cannot_connect_to_a_node_that_closes_the_connection_at_once() -> None:
    # What Docker's port proxy does while the node's container restarts (lab, S19).
    def closed() -> socket.socket:
        client, node = socket.socketpair()
        node.close()
        return client

    api = LibraryMeshApi("node", 4403, open_socket=closed)

    with pytest.raises(UnreachableError, match="cannot connect"):
        await api.connect()


async def test_gives_up_on_a_node_that_does_not_send_its_settings(device: FakeDevice) -> None:
    device.send_config = False

    with pytest.raises(UnreachableError, match="did not send its settings"):
        await api_for(device, timeout=0.3).connect()
    assert device.closed.wait(2)


async def test_gives_up_at_once_on_a_node_that_hangs_up_while_it_sends_them(
    device: FakeDevice,
) -> None:
    device.hang_up_in_config = True
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(UnreachableError, match="closed the connection"):
        await api_for(device, timeout=5).connect()
    assert loop.time() - started < 2


async def test_a_cancelled_connect_closes_what_it_opens(device: FakeDevice) -> None:
    device.config_delay = 0.3  # the connection opens after the cancel
    attempt = asyncio.create_task(api_for(device, timeout=5).connect())
    await asyncio.sleep(0.15)

    attempt.cancel()

    with pytest.raises(asyncio.CancelledError):
        await attempt
    assert await asyncio.to_thread(device.closed.wait, 3)


# Packets from the node.


async def test_hands_over_a_channel_text(device: FakeDevice, connection: MeshConnection) -> None:
    device.push(mesh_packet(ADA, BROADCAST, PORT.TEXT_MESSAGE_APP, "Привіт".encode(), channel=1))

    packet = await next_packet(connection)

    assert packet == Packet(ADA, BROADCAST, 77, Text("Привіт"), channel=1, via_mqtt=True)
    assert not packet.is_direct


async def test_marks_a_reaction(device: FakeDevice, connection: MeshConnection) -> None:
    tapback = mesh_packet(ADA, BROADCAST, PORT.TEXT_MESSAGE_APP, "👍".encode(), channel=1)
    tapback.decoded.emoji = 1
    tapback.decoded.reply_id = 76

    device.push(tapback)
    packet = await next_packet(connection)

    assert packet.payload == Text("👍", reaction=True)


async def test_hands_over_a_direct_message_with_the_senders_key(
    device: FakeDevice, connection: MeshConnection
) -> None:
    sent = mesh_packet(ADA, HUB, PORT.TEXT_MESSAGE_APP, b"hi")
    sent.pki_encrypted = True
    sent.public_key = b"ada key"
    sent.want_ack = True
    sent.hop_start, sent.hop_limit = 3, 2

    device.push(sent)
    packet = await next_packet(connection)

    assert packet.is_direct
    assert (packet.pki, packet.public_key, packet.want_ack) == (True, b"ada key", True)
    assert (packet.hop_start, packet.hop_limit) == (3, 2)


@pytest.mark.parametrize(
    ("error", "name"),
    [
        (mesh_pb2.Routing.Error.NONE, "NONE"),
        (mesh_pb2.Routing.Error.MAX_RETRANSMIT, "MAX_RETRANSMIT"),
        (mesh_pb2.Routing.Error.PKI_UNKNOWN_PUBKEY, "PKI_UNKNOWN_PUBKEY"),
    ],
)
async def test_hands_over_acks_and_naks_with_the_packet_they_answer(
    device: FakeDevice,
    connection: MeshConnection,
    error: "mesh_pb2.Routing.Error.ValueType",
    name: str,
) -> None:
    routing = mesh_pb2.Routing(error_reason=error).SerializeToString()
    device.push(mesh_packet(ADA, HUB, PORT.ROUTING_APP, routing, request_id=4242))

    packet = await next_packet(connection)

    assert (packet.payload, packet.request_id) == (Routing(name), 4242)


async def test_hands_over_nodeinfo(device: FakeDevice, connection: MeshConnection) -> None:
    user = mesh_pb2.User(id="!a1b2c3d4", long_name="Ada", short_name="ADA", public_key=b"k")
    device.push(mesh_packet(ADA, BROADCAST, PORT.NODEINFO_APP, user.SerializeToString()))

    packet = await next_packet(connection)

    assert packet.payload == NodeInfo("Ada", "ADA", b"k")


async def test_hands_over_other_packets_by_their_port(
    device: FakeDevice, connection: MeshConnection
) -> None:
    device.push(mesh_packet(ADA, BROADCAST, PORT.POSITION_APP, b""))
    device.push(mesh_packet(ADA, BROADCAST, PORT.ROUTING_APP, b"\xff\xff"))  # not a Routing
    device.push(mesh_packet(ADA, HUB, PORT.TEXT_MESSAGE_APP, b"caf\xe9"))  # not UTF-8
    encrypted = mesh_pb2.MeshPacket(to=BROADCAST, id=9, encrypted=b"secret")
    device.push(encrypted)

    packets = [await next_packet(connection) for _ in range(4)]

    assert [packet.payload for packet in packets] == [
        Other("POSITION_APP"),
        Other("ROUTING_APP"),
        Text("caf�"),
        Other(""),
    ]


async def test_skips_what_is_not_a_message(device: FakeDevice, connection: MeshConnection) -> None:
    device.send(mesh_pb2.FromRadio())  # an empty message: nothing for anyone
    device.push(mesh_packet(ADA, BROADCAST, PORT.TEXT_MESSAGE_APP, b"after"))

    packet = await next_packet(connection)

    assert packet.payload == Text("after")


# Sending.


async def test_sends_a_text_to_a_channel(device: FakeDevice, connection: MeshConnection) -> None:
    packet_id = await connection.send_text("Привіт", to=BROADCAST, channel=1, want_ack=True)

    [sent] = await asyncio.to_thread(device.wait_for_packets, 1)
    assert sent.id == packet_id
    assert (sent.to, sent.channel, sent.want_ack) == (BROADCAST, 1, True)
    assert sent.decoded.portnum == PORT.TEXT_MESSAGE_APP
    assert sent.decoded.payload.decode() == "Привіт"
    assert sent.hop_limit == 5  # the node's own setting


async def test_sends_a_direct_message(device: FakeDevice, connection: MeshConnection) -> None:
    await connection.send_text("hi", to=ADA, channel=0, want_ack=True)

    [sent] = await asyncio.to_thread(device.wait_for_packets, 1)
    assert (sent.to, sent.channel) == (ADA, 0)


async def test_refuses_a_text_that_does_not_fit_into_a_packet(
    device: FakeDevice, connection: MeshConnection
) -> None:
    with pytest.raises(RejectedError, match="does not fit"):
        await connection.send_text("я" * (MAX_PAYLOAD // 2 + 1), to=ADA, channel=0, want_ack=True)
    assert device.packets() == []


async def sent_admin(
    device: FakeDevice, connection: MeshConnection, command: AdminCommand
) -> admin_pb2.AdminMessage:
    count = len(device.packets())
    packet_id = await connection.send_admin(command)
    sent = (await asyncio.to_thread(device.wait_for_packets, count + 1))[-1]
    assert sent.id == packet_id
    assert (sent.to, sent.want_ack, sent.decoded.want_response) == (HUB, True, True)
    assert sent.decoded.portnum == PORT.ADMIN_APP
    return admin_of(sent)


async def test_sends_the_settings_transaction(
    device: FakeDevice, connection: MeshConnection
) -> None:
    begin = await sent_admin(device, connection, BeginEdit())
    commit = await sent_admin(device, connection, CommitEdit())

    assert begin.begin_edit_settings
    assert commit.commit_edit_settings


async def test_sets_the_names(device: FakeDevice, connection: MeshConnection) -> None:
    admin = await sent_admin(device, connection, SetOwner("chatko", "CHKO"))

    owner = admin.set_owner
    assert (owner.long_name, owner.short_name) == ("chatko", "CHKO")


async def test_sets_the_lora_settings_and_keeps_the_others(
    device: FakeDevice, connection: MeshConnection
) -> None:
    admin = await sent_admin(
        device, connection, SetLora("EU_433", ok_to_mqtt=True, ignore_mqtt=False)
    )

    lora = admin.set_config.lora
    assert lora.region == config_pb2.Config.LoRaConfig.RegionCode.EU_433
    assert (lora.config_ok_to_mqtt, lora.ignore_mqtt, lora.hop_limit) == (True, False, 5)


async def test_refuses_a_region_the_library_does_not_know(connection: MeshConnection) -> None:
    with pytest.raises(RejectedError, match="EU_999"):
        await connection.send_admin(SetLora("EU_999", ok_to_mqtt=True, ignore_mqtt=False))


async def test_sets_the_private_key_for_the_node_to_derive_the_public_one(
    device: FakeDevice, connection: MeshConnection
) -> None:
    admin = await sent_admin(device, connection, SetPrivateKey(b"k" * 32))

    security = admin.set_config.security
    assert (security.private_key, security.public_key) == (b"k" * 32, b"")


async def test_sets_the_mqtt_client_and_keeps_its_other_settings(
    device: FakeDevice, connection: MeshConnection
) -> None:
    wanted = MqttSettings(
        enabled=True,
        address="broker:1884",
        username="u",
        password="p",
        root="msh/EU_433",
        encryption=True,
        tls=True,
    )

    admin = await sent_admin(device, connection, SetMqtt(wanted))

    mqtt = admin.set_module_config.mqtt
    assert (mqtt.address, mqtt.username, mqtt.password, mqtt.root) == (
        "broker:1884",
        "u",
        "p",
        "msh/EU_433",
    )
    assert (mqtt.enabled, mqtt.encryption_enabled, mqtt.tls_enabled) == (True, True, True)
    assert (mqtt.json_enabled, mqtt.proxy_to_client_enabled) == (False, False)
    assert mqtt.map_reporting_enabled


async def test_sets_a_channel_and_keeps_its_other_settings(
    device: FakeDevice, connection: MeshConnection
) -> None:
    wanted = ChannelSettings(1, ChannelRole.SECONDARY, "family", bytes(32), True, True)

    admin = await sent_admin(device, connection, SetChannel(wanted))

    channel = admin.set_channel
    assert (channel.index, channel.role) == (1, channel_pb2.Channel.Role.SECONDARY)
    assert (channel.settings.name, channel.settings.psk) == ("family", bytes(32))
    assert (channel.settings.uplink_enabled, channel.settings.downlink_enabled) == (True, True)
    assert channel.settings.module_settings.position_precision == 13


async def test_turns_a_channel_off(device: FakeDevice, connection: MeshConnection) -> None:
    admin = await sent_admin(device, connection, SetChannel(ChannelSettings(5)))

    channel = admin.set_channel
    assert (channel.index, channel.role, channel.settings.name) == (5, 0, "")


async def test_adds_a_contact_with_what_the_node_knows_of_it(
    device: FakeDevice, connection: MeshConnection
) -> None:
    admin = await sent_admin(device, connection, AddContact(ADA, b"new key", "Ada", "ADA"))

    contact = admin.add_contact
    assert contact.node_num == ADA
    assert (contact.user.id, contact.user.long_name, contact.user.short_name) == (
        "!a1b2c3d4",
        "Ada",
        "ADA",
    )
    assert contact.user.public_key == b"new key"
    assert contact.user.hw_model == mesh_pb2.HardwareModel.PORTDUINO


async def test_adds_a_contact_the_node_has_never_heard(
    device: FakeDevice, connection: MeshConnection
) -> None:
    admin = await sent_admin(device, connection, AddContact(0x0BADC0DE, b"key"))

    assert admin.add_contact.user.id == "!0badc0de"


# The end of a connection.


async def test_ends_when_the_node_hangs_up_and_does_not_connect_again(
    device: FakeDevice, connection: MeshConnection
) -> None:
    device.push(mesh_packet(ADA, BROADCAST, PORT.TEXT_MESSAGE_APP, b"last"))
    device.hang_up()

    assert [packet.payload for packet in await end_of(connection)] == [Text("last")]
    await asyncio.sleep(1.3)  # the library would have connected again after 1 s
    assert device.connections == 1
    with pytest.raises(UnreachableError, match="ended"):
        await connection.send_text("hi", to=ADA, channel=0, want_ack=True)


async def test_ends_when_the_node_says_it_rebooted(
    device: FakeDevice, connection: MeshConnection
) -> None:
    device.send(mesh_pb2.FromRadio(rebooted=True))

    assert await end_of(connection) == []
    assert device.connections == 1


async def test_close_ends_the_connection_and_its_threads(device: FakeDevice) -> None:
    before = set(threading.enumerate())
    connection = await api_for(device).connect()

    await connection.close()
    await connection.close()

    assert await end_of(connection) == []
    assert await asyncio.to_thread(device.closed.wait, 2)
    with pytest.raises(UnreachableError):
        await connection.send_admin(BeginEdit())
    await asyncio.sleep(0.1)
    left = {
        thread
        for thread in set(threading.enumerate()) - before
        if thread.name.startswith(("chatko-meshtastic", "stream reader"))
        or isinstance(thread, threading.Timer)  # the library's heartbeat
    }
    assert not left, left
