"""Shared helpers for the lab's spike scripts (spike S2). Not chatko code.

The scripts that import this module declare `meshtastic` and `paho-mqtt` as their own inline
dependencies, so `uv run lab/<script>.py` works without any project setup.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from meshtastic.protobuf import admin_pb2, mesh_pb2, mqtt_pb2, portnums_pb2
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub

HOST = "127.0.0.1"
HUB_PORT = 4403
RADIO_PORT = 4404
MQTT_PORT = 1883
MQTT_TOPIC = "msh/lab/#"
MQTT_USER = "lab"  # the broker's login for scripts (lab/mosquitto/start.sh)
MQTT_PASSWORD = "chatko-lab-lab"
COMPOSE_FILE = Path(__file__).with_name("docker-compose.yml")

# Fields of a received packet dict worth recording (the library adds decoded and derived keys).
PACKET_KEYS = (
    "from", "fromId", "to", "toId", "id", "channel", "hopLimit", "hopStart", "wantAck",
    "rxTime", "rxSnr", "rxRssi", "viaMqtt", "transportMechanism", "relayNode", "nextHop",
    "pkiEncrypted", "publicKey", "priority",
)  # fmt: skip


def connect(port: int, attempts: int = 30) -> TCPInterface:
    """Connect to a node, waiting while it (re)starts."""
    for attempt in range(attempts):
        try:
            iface = TCPInterface(hostname=HOST, portNumber=port)
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(2)
            continue
        # The library sends its first heartbeat from the reader thread just after the config
        # arrives. Closing before that makes it hit a closed socket and reconnect on its own.
        time.sleep(1)
        return iface
    raise AssertionError("unreachable")


def node_id(num: int) -> str:
    return f"!{num:08x}"


class AdminError(RuntimeError):
    pass


def send_admin(iface: TCPInterface, admin: admin_pb2.AdminMessage, timeout: float = 10.0) -> None:
    """Send an admin message to the connected node and wait for its response.

    The node puts packets addressed to itself into a queue of 4 and drops the oldest when it is
    full, so admin messages sent back to back get lost (spike S2). The node answers each admin
    message that has want_response set, typically within 50 ms, so waiting for that answer is
    enough to pace them.
    """
    done = threading.Event()
    answer: list[dict[str, Any]] = []

    def on_response(packet: dict[str, Any]) -> None:
        answer.append(packet)
        done.set()

    iface.sendData(
        admin,
        iface.localNode.nodeNum,
        portNum=portnums_pb2.PortNum.ADMIN_APP,
        wantAck=True,
        wantResponse=True,
        onResponse=on_response,
        onResponseAckPermitted=True,
    )
    if not done.wait(timeout):
        raise AdminError(f"no response to {admin.WhichOneof('payload_variant')} within {timeout:.0f} s")
    error = answer[0].get("decoded", {}).get("routing", {}).get("errorReason", "NONE")
    if error != "NONE":
        raise AdminError(f"{admin.WhichOneof('payload_variant')} refused: {error}")


def own_user(iface: TCPInterface) -> mesh_pb2.User:
    """The connected node's own User record, as it announces it in NodeInfo."""
    num = iface.localNode.nodeNum
    info = iface.nodesByNum[num]["user"]
    return mesh_pb2.User(
        id=node_id(num),
        long_name=info.get("longName", ""),
        short_name=info.get("shortName", ""),
        hw_model=mesh_pb2.HardwareModel.Value(info.get("hwModel", "UNSET")),
        public_key=iface.localNode.localConfig.security.public_key,
    )


def add_contact_message(num: int, user: mesh_pb2.User) -> admin_pb2.AdminMessage:
    """add_contact: store a node with its public key, as a favorite (never evicted, saved at once)."""
    admin = admin_pb2.AdminMessage()
    admin.add_contact.node_num = num
    admin.add_contact.user.CopyFrom(user)
    return admin


def compose(*args: str) -> None:
    """Run `docker compose` on the lab's compose file."""
    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], check=True)


@dataclass
class Received:
    at: float  # time.monotonic() when the library published the packet
    iface: TCPInterface
    packet: dict[str, Any]

    @property
    def portnum(self) -> str | None:
        return self.packet.get("decoded", {}).get("portnum")

    @property
    def routing_error(self) -> str | None:
        """The error reason of a ROUTING_APP packet ("NONE" for an ACK), else None."""
        routing = self.packet.get("decoded", {}).get("routing")
        if routing is None:
            return None
        return str(routing.get("errorReason", "NONE"))

    @property
    def request_id(self) -> int | None:
        return self.packet.get("decoded", {}).get("requestId")


@dataclass
class Recorder:
    """Every packet the connected nodes hand to their API client, with the time it arrived."""

    received: list[Received] = field(default_factory=list)
    arrived: threading.Condition = field(default_factory=threading.Condition)

    def start(self) -> None:
        pub.subscribe(self._on_receive, "meshtastic.receive")

    def stop(self) -> None:
        pub.unsubscribe(self._on_receive, "meshtastic.receive")

    def _on_receive(self, packet: dict[str, Any], interface: TCPInterface) -> None:
        with self.arrived:
            self.received.append(Received(time.monotonic(), interface, packet))
            self.arrived.notify_all()

    def matching(self, match: Callable[[Received], bool]) -> list[Received]:
        with self.arrived:
            return [r for r in self.received if match(r)]

    def wait_for(self, match: Callable[[Received], bool], timeout: float) -> Received | None:
        deadline = time.monotonic() + timeout
        with self.arrived:
            while True:
                for r in self.received:
                    if match(r):
                        return r
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.arrived.wait(left)


@dataclass
class Envelope:
    at: float
    topic: str
    envelope: mqtt_pb2.ServiceEnvelope
    payload: bytes


class BrokerSniffer:
    """Records (and optionally prints) every ServiceEnvelope the nodes publish to the lab broker."""

    def __init__(self, *, verbose: bool = True) -> None:
        self.verbose = verbose
        self.envelopes: list[Envelope] = []
        self._lock = threading.Lock()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"spike-sniffer-{uuid.uuid4().hex[:6]}"
        )
        self._client.on_message = self._on_message
        self._client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    def start(self) -> None:
        self._client.connect(HOST, MQTT_PORT)
        self._client.subscribe(MQTT_TOPIC)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: bytes) -> None:
        self._client.publish(topic, payload).wait_for_publish(5)

    def _on_message(self, _client: mqtt.Client, _userdata: object, msg: mqtt.MQTTMessage) -> None:
        envelope = mqtt_pb2.ServiceEnvelope()
        try:
            envelope.ParseFromString(msg.payload)
        except Exception:  # noqa: BLE001  # anything that is not an envelope (e.g. map reports)
            return
        with self._lock:
            self.envelopes.append(Envelope(time.monotonic(), msg.topic, envelope, msg.payload))
        if self.verbose:
            print(f"  [mqtt] {describe_envelope(msg.topic, envelope)}")

    def with_packet_id(self, packet_id: int) -> list[Envelope]:
        with self._lock:
            return [e for e in self.envelopes if e.envelope.packet.id == packet_id]


def describe_envelope(topic: str, envelope: mqtt_pb2.ServiceEnvelope) -> str:
    p = envelope.packet
    kind = "decoded" if p.HasField("decoded") else f"encrypted {len(p.encrypted)} B"
    return (
        f"{topic}: channel_id={envelope.channel_id!r} gateway_id={envelope.gateway_id!r} "
        f"from={node_id(getattr(p, 'from'))} to={p.to:#x} id={p.id:#x} channel(hash)={p.channel} "
        f"hop_limit={p.hop_limit} hop_start={p.hop_start} want_ack={p.want_ack} "
        f"pki={p.pki_encrypted} {kind}"
    )


def summary(packet: dict[str, Any]) -> str:
    fields = {k: packet[k] for k in PACKET_KEYS if k in packet}
    decoded = packet.get("decoded", {})
    fields["portnum"] = decoded.get("portnum")
    for key in ("text", "requestId", "routing"):
        if key in decoded:
            value = decoded[key]
            fields[key] = {k: v for k, v in value.items() if k != "raw"} if isinstance(value, dict) else value
    return "\n".join(f"    {k} = {v!r}" for k, v in fields.items())
