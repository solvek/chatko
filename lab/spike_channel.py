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
import time
import uuid

from labkit import (
    HUB_PORT,
    RADIO_PORT,
    BrokerSniffer,
    Recorder,
    connect,
    node_id,
    summary,
)
from meshtastic.tcp_interface import TCPInterface

PRIVATE_INDEX = 1
TIMEOUT = 60.0


def check(sender: TCPInterface, receiver: TCPInterface, recorder: Recorder, label: str) -> bool:
    text = f"S2 {label} {uuid.uuid4().hex[:8]}"
    sent = sender.sendText(text, channelIndex=PRIVATE_INDEX)
    started = time.monotonic()
    print(f"\n{label}: sent {text!r} as packet id {sent.id:#x} from {node_id(sender.localNode.nodeNum)}")
    got = recorder.wait_for(
        lambda r: r.iface is receiver and r.packet.get("decoded", {}).get("text") == text, TIMEOUT
    )
    if got is None:
        print(f"{label}: NOT received within {TIMEOUT:.0f} s")
        return False
    print(f"{label}: received after {got.at - started:.1f} s; packet fields at the receiver:")
    print(summary(got.packet))
    ok = (
        got.packet["from"] == sender.localNode.nodeNum
        and got.packet["id"] == sent.id
        and got.packet.get("channel", 0) == PRIVATE_INDEX
    )
    print(f"{label}: from, id and channel index match: {ok}")
    return ok


def main() -> int:
    recorder = Recorder()
    recorder.start()
    sniffer = BrokerSniffer()
    sniffer.start()
    hub = connect(HUB_PORT)
    radio = connect(RADIO_PORT)
    try:
        print(f"hub   = {node_id(hub.localNode.nodeNum)} {hub.getLongName()!r}")
        print(f"radio = {node_id(radio.localNode.nodeNum)} {radio.getLongName()!r}")
        up = check(radio, hub, recorder, "radio->hub")
        down = check(hub, radio, recorder, "hub->radio")
        time.sleep(1)  # let the sniffer print the last envelopes
        print(f"\nresult: radio->hub {'OK' if up else 'FAIL'}, hub->radio {'OK' if down else 'FAIL'}")
        return 0 if up and down else 1
    finally:
        hub.close()
        radio.close()
        sniffer.stop()
        recorder.stop()


if __name__ == "__main__":
    sys.exit(main())
