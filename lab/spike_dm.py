# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Spike S2, part 2: PKI direct messages between the lab's nodes through MQTT.

Checks, in order:
  keys     each node's public key and whether it knows the other node's key;
  dm       a direct text radio -> hub and hub -> radio with want_ack: the fields the receiver's
           API client sees, the MQTT envelopes, and every ACK/NAK the sender's client gets;
  rate     two direct texts from one client less than 2 s apart;
  dup      the radio's direct message published again by two made-up gateways (another gateway
           id, a lower hop limit): how often the hub's client sees it, and with which (from, id);
  offline  (only with --offline) the radio container is stopped, the hub sends a direct text, and
           the script records what the hub's client gets until the firmware gives up.

Run `lab/provision.py` first.

    uv run lab/spike_dm.py [--offline]
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

from labkit import (
    HUB_PORT,
    RADIO_PORT,
    BrokerSniffer,
    Received,
    Recorder,
    compose,
    connect,
    describe_envelope,
    node_id,
    summary,
)
from meshtastic.protobuf import mqtt_pb2
from meshtastic.tcp_interface import TCPInterface

TIMEOUT = 30.0
OFFLINE_TIMEOUT = 240.0


def show_keys(a: TCPInterface, b: TCPInterface) -> bool:
    ok = True
    for me, other in ((a, b), (b, a)):
        mine = me.localNode.localConfig.security.public_key.hex()
        entry = me.nodesByNum.get(other.localNode.nodeNum, {})
        known = entry.get("user", {}).get("publicKey")
        print(f"{node_id(me.localNode.nodeNum)}: own public key {mine[:16]}…")
        print(f"    knows {node_id(other.localNode.nodeNum)}: user={'user' in entry} publicKey={known}")
        ok = ok and known is not None
    return ok


def responses(recorder: Recorder, sender: TCPInterface, packet_id: int) -> list[Received]:
    """Routing packets (ACK/NAK) the sender's client got for one of its packets."""
    return recorder.matching(
        lambda r: r.iface is sender and r.request_id == packet_id and r.routing_error is not None
    )


def print_responses(sender: TCPInterface, found: list[Received], started: float) -> None:
    me = sender.localNode.nodeNum
    for r in found:
        origin = "implicit (own node)" if r.packet["from"] == me else f"from {node_id(r.packet['from'])}"
        print(
            f"    +{r.at - started:5.1f} s  {r.routing_error:<24} {origin}  "
            f"transport={r.packet.get('transportMechanism')} viaMqtt={r.packet.get('viaMqtt')}"
        )


def is_final(r: Received, sender: TCPInterface) -> bool:
    """A real ACK from the destination, or any NAK, ends the wait."""
    return r.routing_error != "NONE" or r.packet["from"] != sender.localNode.nodeNum


def direct(
    sender: TCPInterface,
    receiver: TCPInterface,
    recorder: Recorder,
    sniffer: BrokerSniffer,
    label: str,
) -> tuple[bool, int]:
    dest = receiver.localNode.nodeNum
    text = f"S2 DM {label} {uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    sent = sender.sendText(text, destinationId=dest, wantAck=True)
    print(f"\n{label}: sent {text!r} as packet id {sent.id:#x}, want_ack")
    got = recorder.wait_for(
        lambda r: r.iface is receiver and r.packet.get("decoded", {}).get("text") == text, TIMEOUT
    )
    final = recorder.wait_for(
        lambda r: (
            r.iface is sender
            and r.request_id == sent.id
            and r.routing_error is not None
            and is_final(r, sender)
        ),
        TIMEOUT,
    )
    time.sleep(1)  # let late ACKs and envelopes arrive
    if got is None:
        print(f"{label}: NOT received within {TIMEOUT:.0f} s")
    else:
        print(f"{label}: received after {got.at - started:.2f} s; packet fields at the receiver:")
        print(summary(got.packet))
    print(f"{label}: ACK/NAK at the sender:")
    print_responses(sender, responses(recorder, sender, sent.id), started)
    print(f"{label}: envelopes with this packet id: {len(sniffer.with_packet_id(sent.id))}")
    ok = (
        got is not None
        and got.packet.get("pkiEncrypted") is True
        and got.packet["id"] == sent.id
        and final is not None
        and final.routing_error == "NONE"
        and final.packet["from"] == dest
    )
    print(f"{label}: PKI, id and a real ACK from the destination: {ok}")
    return ok, sent.id


def rate_limit(sender: TCPInterface, receiver: TCPInterface, recorder: Recorder) -> None:
    dest = receiver.localNode.nodeNum
    time.sleep(3)  # start outside any earlier text's 2 s window
    first = sender.sendText(f"S2 rate 1 {uuid.uuid4().hex[:6]}", destinationId=dest, wantAck=True)
    time.sleep(0.5)
    second = sender.sendText(f"S2 rate 2 {uuid.uuid4().hex[:6]}", destinationId=dest, wantAck=True)
    started = time.monotonic()
    recorder.wait_for(
        lambda r: r.iface is sender and r.request_id == second.id and r.routing_error is not None, 10
    )
    time.sleep(3)
    print("\nrate: two direct texts 0.5 s apart from one client")
    for label, packet in (("first", first), ("second", second)):
        print(f"  {label} ({packet.id:#x}):")
        print_responses(sender, responses(recorder, sender, packet.id), started)


def duplicates(hub: TCPInterface, recorder: Recorder, sniffer: BrokerSniffer, packet_id: int) -> None:
    originals = [e for e in sniffer.with_packet_id(packet_id) if e.topic.split("/")[-2] == "PKI"]
    if not originals:
        print("\ndup: no PKI envelope of the radio's message was captured")
        return
    original = originals[0]
    before = len(recorder.matching(lambda r: r.iface is hub and r.packet.get("id") == packet_id))
    print(f"\ndup: original {describe_envelope(original.topic, original.envelope)}")
    for n, gateway in enumerate(("!c4a7b0f1", "!c4a7b0f2"), start=1):
        copy = mqtt_pb2.ServiceEnvelope()
        copy.CopyFrom(original.envelope)
        copy.gateway_id = gateway
        copy.packet.hop_limit = max(0, original.envelope.packet.hop_limit - n)
        topic = original.topic.rsplit("/", 1)[0] + "/" + gateway
        sniffer.publish(topic, copy.SerializeToString())
    time.sleep(5)
    seen = recorder.matching(lambda r: r.iface is hub and r.packet.get("id") == packet_id)
    print(
        f"dup: the hub's client saw packet {packet_id:#x} {before} time(s) before and "
        f"{len(seen)} time(s) after two more gateways published it"
    )
    for r in seen:
        print(
            f"    from={node_id(r.packet['from'])} id={r.packet['id']:#x} hopLimit={r.packet.get('hopLimit')}"
        )


def offline(hub: TCPInterface, radio_num: int, recorder: Recorder, sniffer: BrokerSniffer) -> None:
    print("\noffline: stopping the radio container")
    compose("stop", "radio")
    try:
        time.sleep(2)
        started = time.monotonic()
        sent = hub.sendText(f"S2 offline {uuid.uuid4().hex[:6]}", destinationId=radio_num, wantAck=True)
        print(
            f"offline: sent packet id {sent.id:#x} to {node_id(radio_num)}, waiting up to {OFFLINE_TIMEOUT:.0f} s"
        )
        recorder.wait_for(
            lambda r: r.iface is hub and r.request_id == sent.id and r.routing_error not in (None, "NONE"),
            OFFLINE_TIMEOUT,
        )
        print("offline: ACK/NAK at the hub:")
        print_responses(hub, responses(recorder, hub, sent.id), started)
        publications = sniffer.with_packet_id(sent.id)
        print(f"offline: the hub published the packet {len(publications)} time(s):")
        for e in publications:
            print(f"    +{e.at - started:5.1f} s  {e.topic}")
    finally:
        print("offline: starting the radio container again")
        compose("start", "radio")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--offline", action="store_true", help="also run the offline-radio check")
    args = parser.parse_args()

    recorder = Recorder()
    recorder.start()
    sniffer = BrokerSniffer()
    sniffer.start()
    hub = connect(HUB_PORT)
    radio = connect(RADIO_PORT)
    radio_num = radio.localNode.nodeNum
    try:
        keys = show_keys(hub, radio)
        up, up_id = direct(radio, hub, recorder, sniffer, "radio->hub")
        down, _ = direct(hub, radio, recorder, sniffer, "hub->radio")
        rate_limit(hub, radio, recorder)
        duplicates(hub, recorder, sniffer, up_id)
    finally:
        radio.close()
    try:
        if args.offline:
            offline(hub, radio_num, recorder, sniffer)
    finally:
        hub.close()
        sniffer.stop()
        recorder.stop()
    print(
        f"\nresult: keys {'OK' if keys else 'MISSING'}, radio->hub {'OK' if up else 'FAIL'}, "
        f"hub->radio {'OK' if down else 'FAIL'}"
    )
    return 0 if keys and up and down else 1


if __name__ == "__main__":
    sys.exit(main())
