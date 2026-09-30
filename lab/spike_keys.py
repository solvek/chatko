# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Spike S2, part 2: how the lab's nodes learn each other's public keys.

    uv run lab/spike_keys.py show            # each node's id, own key and node database
    uv run lab/spike_keys.py learn           # forget keys and watch how they come back
    uv run lab/spike_keys.py contact hub     # hub adds the radio's current key (add_contact)
    uv run lab/spike_keys.py contact radio   # radio adds the hub's current key (add_contact)

`learn` runs these steps; each prints what the sender's API client gets:
  1. the hub forgets the radio, then sends it a direct text;
  2. the hub asks the radio for its NodeInfo (a NODEINFO_APP packet with want_response);
  3. the hub adds the radio's key with add_contact, then sends a direct text;
  4. the radio forgets the hub, then the hub sends it a direct text, and again after the firmware
     has reacted to the radio's NAK.
`learn` leaves the radio in the hub's node database as a favorite contact.
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
import uuid

from labkit import (
    HUB_PORT,
    RADIO_PORT,
    Received,
    Recorder,
    add_contact_message,
    connect,
    node_id,
    own_user,
    send_admin,
)
from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2
from meshtastic.tcp_interface import TCPInterface

TIMEOUT = 30.0
TEXT_INTERVAL = 2.5  # the firmware drops a client's text sent less than 2 s after the previous one


def show(iface: TCPInterface) -> None:
    me = iface.localNode.nodeNum
    own = base64.b64encode(iface.localNode.localConfig.security.public_key).decode()
    print(f"{node_id(me)} ({iface.getLongName()!r}): own public key {own}")
    for num, n in sorted(iface.nodesByNum.items()):
        if num == me:
            continue
        u = n.get("user", {})
        print(
            f"    {node_id(num)} {u.get('longName')!r}/{u.get('shortName')!r} publicKey={u.get('publicKey')} "
            f"favorite={n.get('isFavorite', False)} lastHeard={n.get('lastHeard')} viaMqtt={n.get('viaMqtt')}"
        )


def forget(iface: TCPInterface, num: int) -> None:
    print(f"  {node_id(iface.localNode.nodeNum)} forgets {node_id(num)} (remove_by_nodenum)")
    admin = admin_pb2.AdminMessage()
    admin.remove_by_nodenum = num
    send_admin(iface, admin)


def add_contact(iface: TCPInterface, num: int, user: mesh_pb2.User) -> None:
    print(
        f"  {node_id(iface.localNode.nodeNum)} adds {node_id(num)} with key {user.public_key.hex()[:16]}… (add_contact)"
    )
    send_admin(iface, add_contact_message(num, user))


def direct(sender: TCPInterface, dest: int, recorder: Recorder) -> str:
    """Send a direct text with want_ack; return the first real ACK or NAK the sender's client gets."""
    time.sleep(TEXT_INTERVAL)
    me = sender.localNode.nodeNum
    started = time.monotonic()
    sent = sender.sendText(f"S2 keys {uuid.uuid4().hex[:6]}", destinationId=dest, wantAck=True)

    def final(r: Received) -> bool:
        return (
            r.iface is sender
            and r.request_id == sent.id
            and r.routing_error is not None
            and (r.routing_error != "NONE" or r.packet["from"] != me)
        )

    got = recorder.wait_for(final, TIMEOUT)
    if got is None:
        outcome = f"no ACK or NAK within {TIMEOUT:.0f} s"
    else:
        origin = "own node" if got.packet["from"] == me else node_id(got.packet["from"])
        kind = "ACK" if got.routing_error == "NONE" else f"NAK {got.routing_error}"
        outcome = f"{kind} from {origin} after {got.at - started:.1f} s"
    print(f"  {node_id(me)} -> {node_id(dest)} direct text {sent.id:#x}: {outcome}")
    return outcome


def request_nodeinfo(asker: TCPInterface, dest: int, recorder: Recorder) -> float | None:
    """Send our NodeInfo with want_response; return the seconds until the other node's NodeInfo arrives."""
    started = time.monotonic()
    asker.sendData(own_user(asker), dest, portNum=portnums_pb2.PortNum.NODEINFO_APP, wantResponse=True)
    got = recorder.wait_for(
        lambda r: (
            r.iface is asker and r.at >= started and r.portnum == "NODEINFO_APP" and r.packet["from"] == dest
        ),
        TIMEOUT,
    )
    took = None if got is None else got.at - started
    print(
        f"  {node_id(asker.localNode.nodeNum)} asks {node_id(dest)} for NodeInfo: "
        + ("no reply" if took is None else f"reply after {took:.1f} s")
    )
    return took


def wait_nodeinfo(receiver: TCPInterface, sender: int, recorder: Recorder, since: float) -> None:
    got = recorder.wait_for(
        lambda r: (
            r.iface is receiver
            and r.at >= since
            and r.portnum == "NODEINFO_APP"
            and r.packet["from"] == sender
        ),
        TIMEOUT,
    )
    print(
        f"  {node_id(receiver.localNode.nodeNum)} got NodeInfo from {node_id(sender)}: "
        + ("no" if got is None else f"after {got.at - since:.1f} s, to={got.packet['toId']}")
    )


def learn(hub: TCPInterface, radio: TCPInterface, recorder: Recorder) -> None:
    hub_num, radio_num = hub.localNode.nodeNum, radio.localNode.nodeNum
    radio_user = own_user(radio)

    print("\n1. The hub does not know the radio's key")
    forget(hub, radio_num)
    direct(hub, radio_num, recorder)

    print("\n2. The hub asks the radio for its NodeInfo")
    request_nodeinfo(hub, radio_num, recorder)
    direct(hub, radio_num, recorder)

    print("\n3. The hub adds the radio's key from outside (add_contact)")
    add_contact(hub, radio_num, radio_user)
    direct(hub, radio_num, recorder)

    print("\n4. The radio does not know the hub's key")
    forget(radio, hub_num)
    since = time.monotonic()
    direct(hub, radio_num, recorder)
    wait_nodeinfo(radio, hub_num, recorder, since)
    direct(hub, radio_num, recorder)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("learn")
    contact = sub.add_parser("contact")
    contact.add_argument("node", choices=("hub", "radio"), help="the node that adds the other one's key")
    args = parser.parse_args()

    recorder = Recorder()
    recorder.start()
    hub = connect(HUB_PORT)
    radio = connect(RADIO_PORT)
    try:
        if args.command == "show":
            show(hub)
            show(radio)
        elif args.command == "learn":
            learn(hub, radio, recorder)
        else:
            me, other = (hub, radio) if args.node == "hub" else (radio, hub)
            add_contact(me, other.localNode.nodeNum, own_user(other))
            direct(me, other.localNode.nodeNum, recorder)
    finally:
        hub.close()
        radio.close()
        recorder.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
