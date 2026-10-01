"""A Meshtastic node at the other end of a socket pair, for testing the adapter with the real
library and no network.

It speaks the stream protocol of a node's TCP API: frames of 0x94 0xC3, a two-byte length and a
protobuf (`ToRadio` from the client, `FromRadio` to it). On `want_config_id` it sends its node
number, node database, settings and channels, as `meshtasticd` does, and it answers each admin
message. Everything else is up to the test: `push` hands the client a packet, `hang_up` closes
the connection as a rebooting node does.
"""

import socket
import struct
import threading
import time

from meshtastic.protobuf import (
    admin_pb2,
    channel_pb2,
    config_pb2,
    mesh_pb2,
    module_config_pb2,
    portnums_pb2,
)

HUB = 0xC4A7B001
START1, START2 = 0x94, 0xC3


def frame(message: mesh_pb2.FromRadio) -> bytes:
    data = message.SerializeToString()
    return bytes([START1, START2]) + struct.pack(">H", len(data)) + data


def take_frame(buffer: bytes) -> tuple[bytes | None, bytes]:
    """The first frame in the buffer and what follows it, skipping bytes that start none."""
    while buffer:
        if buffer[0] != START1:
            buffer = buffer[1:]
            continue
        if len(buffer) < 4:
            return None, buffer
        if buffer[1] != START2:
            buffer = buffer[1:]
            continue
        (length,) = struct.unpack(">H", buffer[2:4])
        if len(buffer) < 4 + length:
            return None, buffer
        return buffer[4 : 4 + length], buffer[4 + length :]
    return None, buffer


def node_info(num: int, long_name: str, short_name: str, key: bytes = b"") -> mesh_pb2.NodeInfo:
    info = mesh_pb2.NodeInfo(num=num)
    info.user.id = f"!{num:08x}"
    info.user.long_name = long_name
    info.user.short_name = short_name
    info.user.public_key = key
    info.user.hw_model = mesh_pb2.HardwareModel.PORTDUINO
    return info


class FakeDevice:
    """One node. Each `open_socket` is a new connection to it, served by a thread."""

    def __init__(self, num: int = HUB) -> None:
        self.num = num
        self.nodes = [node_info(num, "chatko hub", "HUB", b"hub public key")]
        self.lora = config_pb2.Config.LoRaConfig(
            region=config_pb2.Config.LoRaConfig.RegionCode.EU_868,
            hop_limit=5,
            config_ok_to_mqtt=True,
        )
        self.security = config_pb2.Config.SecurityConfig(
            private_key=b"hub private key", public_key=b"hub public key"
        )
        self.mqtt = module_config_pb2.ModuleConfig.MQTTConfig(
            enabled=True,
            address="mosquitto",
            username="hub",
            password="pw",
            root="msh/lab",
            encryption_enabled=True,
            map_reporting_enabled=True,
        )
        primary = channel_pb2.Channel(index=0, role=channel_pb2.Channel.Role.PRIMARY)
        primary.settings.psk = b"\x01"
        primary.settings.uplink_enabled = primary.settings.downlink_enabled = True
        family = channel_pb2.Channel(index=1, role=channel_pb2.Channel.Role.SECONDARY)
        family.settings.name = "Family"
        family.settings.psk = bytes(16)
        family.settings.module_settings.position_precision = 13
        self.channels = [primary, family]
        self.send_config = True
        """False: the node never says its config is complete."""
        self.hang_up_in_config = False
        self.config_delay = 0.0
        """Seconds the node takes before it sends its config."""
        self.answer_admin = True
        self.received: list[mesh_pb2.ToRadio] = []
        self.connections = 0
        self.closed = threading.Event()
        """The client closed the latest connection."""
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None

    # The adapter's side.

    def open_socket(self) -> socket.socket:
        client, device = socket.socketpair()
        with self._lock:
            self.connections += 1
            self._socket = device
            self.closed.clear()
        threading.Thread(target=self._serve, args=(device,), daemon=True).start()
        return client

    # The test's side.

    def push(self, packet: mesh_pb2.MeshPacket) -> None:
        self.send(mesh_pb2.FromRadio(packet=packet))

    def send(self, message: mesh_pb2.FromRadio) -> None:
        with self._lock:
            sock = self._socket
        assert sock is not None
        sock.sendall(frame(message))

    def hang_up(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)

    def packets(self) -> list[mesh_pb2.MeshPacket]:
        with self._lock:
            return [message.packet for message in self.received if message.HasField("packet")]

    def wait_for_packets(self, count: int, within: float = 2.0) -> list[mesh_pb2.MeshPacket]:
        deadline = time.monotonic() + within
        while len(packets := self.packets()) < count:
            if time.monotonic() > deadline:
                raise AssertionError(f"{len(packets)} packets, not {count}")
            time.sleep(0.005)
        return packets

    # The node at work.

    def _serve(self, sock: socket.socket) -> None:
        buffer = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while True:
                data, buffer = take_frame(buffer)
                if data is None:
                    break
                message = mesh_pb2.ToRadio()
                message.ParseFromString(data)
                with self._lock:
                    self.received.append(message)
                self._handle(sock, message)
        sock.close()  # as a node does when its client hangs up
        self.closed.set()

    def _handle(self, sock: socket.socket, message: mesh_pb2.ToRadio) -> None:
        kind = message.WhichOneof("payload_variant")
        if kind == "want_config_id":
            self._send_config(sock, message.want_config_id)
        elif kind == "packet":
            packet = message.packet
            if packet.decoded.portnum == portnums_pb2.PortNum.ADMIN_APP and self.answer_admin:
                answer = mesh_pb2.MeshPacket(to=self.num, channel=0)
                setattr(answer, "from", self.num)  # `from` is a keyword
                answer.decoded.portnum = portnums_pb2.PortNum.ROUTING_APP
                answer.decoded.request_id = packet.id
                answer.decoded.payload = mesh_pb2.Routing(
                    error_reason=mesh_pb2.Routing.Error.NONE
                ).SerializeToString()
                sock.sendall(frame(mesh_pb2.FromRadio(packet=answer)))

    def _send_config(self, sock: socket.socket, config_id: int) -> None:
        time.sleep(self.config_delay)
        messages = [mesh_pb2.FromRadio(my_info=mesh_pb2.MyNodeInfo(my_node_num=self.num))]
        messages += [mesh_pb2.FromRadio(node_info=info) for info in self.nodes]
        messages.append(mesh_pb2.FromRadio(config=config_pb2.Config(lora=self.lora)))
        messages.append(mesh_pb2.FromRadio(config=config_pb2.Config(security=self.security)))
        messages.append(
            mesh_pb2.FromRadio(moduleConfig=module_config_pb2.ModuleConfig(mqtt=self.mqtt))
        )
        messages += [mesh_pb2.FromRadio(channel=channel) for channel in self.channels]
        if self.hang_up_in_config:
            sock.sendall(b"".join(frame(message) for message in messages[:2]))
            sock.shutdown(socket.SHUT_RDWR)
            return
        if self.send_config:
            messages.append(mesh_pb2.FromRadio(config_complete_id=config_id))
        sock.sendall(b"".join(frame(message) for message in messages))


def admin_of(packet: mesh_pb2.MeshPacket) -> admin_pb2.AdminMessage:
    admin = admin_pb2.AdminMessage()
    admin.ParseFromString(packet.decoded.payload)
    return admin


def mesh_packet(
    sender: int,
    to: int,
    port: "portnums_pb2.PortNum.ValueType",
    payload: bytes,
    *,
    packet_id: int = 77,
    channel: int = 0,
    request_id: int = 0,
) -> mesh_pb2.MeshPacket:
    packet = mesh_pb2.MeshPacket(to=to, id=packet_id, channel=channel, via_mqtt=True)
    setattr(packet, "from", sender)  # `from` is a keyword
    packet.decoded.portnum = port
    packet.decoded.payload = payload
    packet.decoded.request_id = request_id
    return packet
