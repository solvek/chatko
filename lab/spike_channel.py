# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Spike S2: private-channel text radio -> hub and hub -> radio through the lab's MQTT broker.

Connects to both virtual nodes over TCP (as chatko will), sends a text on the private channel from
one node, waits until the other node reports it, and prints what the Python API and the broker show.
Run `lab/provision.py` first.

    uv run lab/spike_channel.py
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import paho.mqtt.client as mqtt
from meshtastic.protobuf import mqtt_pb2
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub

PRIVATE_INDEX = 1
MQTT_HOST = "127.0.0.1"
MQTT_TOPIC = "msh/lab/#"
TIMEOUT = 60.0

# Fields of the received packet dict worth recording (the library adds decoded/derived keys).
PACKET_KEYS = (
    "from", "fromId", "to", "toId", "id", "channel", "hopLimit", "hopStart", "wantAck",
    "rxTime", "rxSnr", "rxRssi", "viaMqtt", "transportMechanism", "relayNode", "nextHop",
    "pkiEncrypted", "publicKey", "priority",
)  # fmt: skip


@dataclass
class Inbox:
    """Text packets received by one node, filled from the library's receive thread."""

    packets: list[dict[str, Any]] = field(default_factory=list)
    arrived: threading.Condition = field(default_factory=threading.Condition)

    def add(self, packet: dict[str, Any]) -> None:
        with self.arrived:
            self.packets.append(packet)
            self.arrived.notify_all()

    def wait_for(self, text: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self.arrived:
            while True:
                for packet in self.packets:
                    if packet.get("decoded", {}).get("text") == text:
                        return packet
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.arrived.wait(left)


def sniff_broker() -> mqtt.Client:
    """Print every envelope the nodes publish, to see topics and outer packet fields."""

    def on_message(_client: mqtt.Client, _userdata: object, msg: mqtt.MQTTMessage) -> None:
        envelope = mqtt_pb2.ServiceEnvelope()
        try:
            envelope.ParseFromString(msg.payload)
        except Exception:  # noqa: BLE001  # anything that is not an envelope (e.g. map reports)
            print(f"  [mqtt] {msg.topic}: {len(msg.payload)} bytes, not a ServiceEnvelope")
            return
        p = envelope.packet
        kind = "decoded" if p.HasField("decoded") else f"encrypted {len(p.encrypted)} B"
        print(
            f"  [mqtt] {msg.topic}: channel_id={envelope.channel_id!r} "
            f"gateway_id={envelope.gateway_id!r} from=!{getattr(p, 'from'):08x} to={p.to:#x} "
            f"id={p.id:#x} channel(hash)={p.channel} hop_limit={p.hop_limit} "
            f"hop_start={p.hop_start} want_ack={p.want_ack} pki={p.pki_encrypted} {kind}"
        )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"spike-sniffer-{uuid.uuid4().hex[:6]}")
    client.on_message = on_message
    client.connect(MQTT_HOST, 1883)
    client.subscribe(MQTT_TOPIC)
    client.loop_start()
    return client


def summary(packet: dict[str, Any]) -> str:
    fields = {k: packet[k] for k in PACKET_KEYS if k in packet}
    decoded = packet.get("decoded", {})
    fields["portnum"] = decoded.get("portnum")
    fields["text"] = decoded.get("text")
    return "\n".join(f"    {k} = {v!r}" for k, v in fields.items())


def check(sender: TCPInterface, receiver: TCPInterface, inbox: Inbox, label: str) -> bool:
    text = f"S2 {label} {uuid.uuid4().hex[:8]}"
    sent = sender.sendText(text, channelIndex=PRIVATE_INDEX)
    started = time.monotonic()
    print(f"\n{label}: sent {text!r} as packet id {sent.id:#x} from {sender.localNode.nodeNum:#x}")
    packet = inbox.wait_for(text, TIMEOUT)
    if packet is None:
        print(f"{label}: NOT received within {TIMEOUT:.0f} s")
        return False
    elapsed = time.monotonic() - started
    print(f"{label}: received after {elapsed:.1f} s; packet fields at the receiver:")
    print(summary(packet))
    ok = (
        packet["from"] == sender.localNode.nodeNum
        and packet["id"] == sent.id
        and packet.get("channel", 0) == PRIVATE_INDEX
        and receiver.localNode.nodeNum != sender.localNode.nodeNum
    )
    print(f"{label}: from, id and channel index match: {ok}")
    return ok


def main() -> int:
    sniffer = sniff_broker()
    hub = TCPInterface(hostname="127.0.0.1", portNumber=4403)
    radio = TCPInterface(hostname="127.0.0.1", portNumber=4404)
    inboxes = {id(hub): Inbox(), id(radio): Inbox()}

    def on_text(packet: dict[str, Any], interface: TCPInterface) -> None:
        inboxes[id(interface)].add(packet)

    pub.subscribe(on_text, "meshtastic.receive.text")
    try:
        print(f"hub   = !{hub.localNode.nodeNum:08x} {hub.getLongName()!r}")
        print(f"radio = !{radio.localNode.nodeNum:08x} {radio.getLongName()!r}")
        up = check(radio, hub, inboxes[id(hub)], "radio->hub")
        down = check(hub, radio, inboxes[id(radio)], "hub->radio")
        time.sleep(1)  # let the sniffer print the last envelopes
        print(f"\nresult: radio->hub {'OK' if up else 'FAIL'}, hub->radio {'OK' if down else 'FAIL'}")
        return 0 if up and down else 1
    finally:
        hub.close()
        radio.close()
        sniffer.loop_stop()
        sniffer.disconnect()


if __name__ == "__main__":
    sys.exit(main())
