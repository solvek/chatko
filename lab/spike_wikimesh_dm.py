# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Spike S23 (D62): direct messages from the lab's `wikimesh` probe to the owner's physical node.

The probe (`lab/spike_wikimesh.py`) and the owner's node share a private channel at index 2 (the
test channel). The probe is given the owner's public key as a contact, announces its own NodeInfo
on the private channel (so the owner's node learns the key) and then sends `--count` direct texts
with want_ack, one per `--gap` seconds, and prints which of them the sender's client saw ACKed.

    uv run lab/spike_wikimesh_dm.py --owner-id '!xxxxxxxx' --owner-key BASE64 [--count 5] [--gap 20]
"""

from __future__ import annotations

import argparse
import base64
import sys
import threading
import time

from labkit import add_contact_message, connect, own_user, send_admin
from meshtastic.protobuf import mesh_pb2, portnums_pb2

PORT = 4406
PRIVATE_INDEX = 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner-id", required=True, help="the owner's node id, e.g. !4d80f899")
    parser.add_argument("--owner-key", required=True, help="the owner's public key, base64")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--gap", type=float, default=20.0)
    args = parser.parse_args()
    owner_num = int(args.owner_id.lstrip("!"), 16)

    iface = connect(PORT)
    try:
        contact = mesh_pb2.User(id=args.owner_id, public_key=base64.b64decode(args.owner_key))
        send_admin(iface, add_contact_message(owner_num, contact))
        print("owner added as a contact on the probe")

        # NodeInfo on the private channel, a few times: a broadcast has no ACK and a third is lost.
        for attempt in range(3):
            iface.sendData(
                own_user(iface),
                destinationId="^all",
                portNum=portnums_pb2.PortNum.NODEINFO_APP,
                channelIndex=PRIVATE_INDEX,
            )
            print(f"NodeInfo {attempt + 1}/3 sent on channel {PRIVATE_INDEX}")
            time.sleep(10)
        time.sleep(15)

        results: dict[int, str] = {}
        done: dict[int, threading.Event] = {}

        def on_ack(index: int):
            def callback(packet: dict) -> None:
                routing = packet.get("decoded", {}).get("routing", {})
                results[index] = routing.get("errorReason", "?")
                done[index].set()

            return callback

        for i in range(1, args.count + 1):
            done[i] = threading.Event()
            iface.sendData(
                f"dm{i}".encode(),
                destinationId=args.owner_id,
                portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                wantAck=True,
                onResponse=on_ack(i),
                onResponseAckPermitted=True,  # without it the library ignores a plain ACK
            )
            print(f"dm{i} sent")
            time.sleep(args.gap)
        time.sleep(30)  # late ACKs
        for i in range(1, args.count + 1):
            print(f"dm{i}: {results.get(i, 'no answer')}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
