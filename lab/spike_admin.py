# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Spike S2, part 2: why admin messages sent back to back get lost, and how to pace them.

Sends harmless admin requests (get_config DEVICE_CONFIG, want_response) to the hub's node twice:
first back to back, then one at a time, waiting for each response. Prints how many got a response
and how long each response took. With `Logging: LogLevel: debug` in the node's YAML, the node's log
shows "fromRadioQ full, drop oldest!" for the lost ones.

    uv run lab/spike_admin.py [--count 10] [--port 4403]
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

from labkit import HUB_PORT, connect
from meshtastic.protobuf import admin_pb2, portnums_pb2
from meshtastic.tcp_interface import TCPInterface

TIMEOUT = 10.0


def request(iface: TCPInterface, done: threading.Event) -> None:
    admin = admin_pb2.AdminMessage()
    admin.get_config_request = admin_pb2.AdminMessage.ConfigType.DEVICE_CONFIG
    iface.sendData(
        admin,
        iface.localNode.nodeNum,
        portNum=portnums_pb2.PortNum.ADMIN_APP,
        wantAck=True,
        wantResponse=True,
        onResponse=lambda _packet: done.set(),
        onResponseAckPermitted=True,
    )


def back_to_back(iface: TCPInterface, count: int) -> None:
    events = [threading.Event() for _ in range(count)]
    for event in events:
        request(iface, event)
    time.sleep(TIMEOUT)
    answered = sum(e.is_set() for e in events)
    print(f"back to back: {answered} of {count} requests got a response")


def one_at_a_time(iface: TCPInterface, count: int) -> None:
    took: list[float] = []
    for _ in range(count):
        event = threading.Event()
        started = time.monotonic()
        request(iface, event)
        if event.wait(TIMEOUT):
            took.append(time.monotonic() - started)
    if took:
        print(
            f"one at a time: {len(took)} of {count} got a response; "
            f"response time min {min(took) * 1000:.0f} ms, max {max(took) * 1000:.0f} ms"
        )
    else:
        print(f"one at a time: 0 of {count} got a response")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--port", type=int, default=HUB_PORT)
    args = parser.parse_args()
    iface = connect(args.port)
    try:
        back_to_back(iface, args.count)
        one_at_a_time(iface, args.count)
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
