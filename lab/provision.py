# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Provision the lab's virtual nodes over their TCP API (spike S2).

Sets each node's names, region, private key, private channel and MQTT client (with the node's own
login to the lab's Mosquitto), so that the nodes reach each other through the broker, then gives each node the others' public keys as
a contact, so direct messages work at once. Idempotent: a node whose settings already match is
left alone and not rebooted.

    uv run lab/provision.py [--only hub|radio|radio2] [--no-contacts]

--only provisions one node, e.g. after its volume was wiped. --no-contacts skips the contacts and
leaves key discovery to NodeInfo (see spike_keys.py).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import time
from dataclasses import dataclass

from labkit import add_contact_message, connect, node_id, own_user, send_admin
from meshtastic.protobuf import admin_pb2, channel_pb2, config_pb2
from meshtastic.tcp_interface import TCPInterface

# Lab values only. The PSK and the private keys are derived from public strings; never use them
# on a real mesh.
PRIVATE_CHANNEL = "Family"
PRIVATE_PSK = hashlib.sha256(b"chatko-lab-family").digest()
PRIVATE_INDEX = 1
MQTT_ADDRESS = "mosquitto"  # the broker's name inside the compose network
MQTT_ROOT = "msh/lab"
REGION = config_pb2.Config.LoRaConfig.RegionCode.EU_868
REBOOT_WAIT = 10.0  # seconds; a node reboots 7 s after a commit, and Docker restarts it


@dataclass(frozen=True)
class LabNode:
    name: str
    port: int
    long_name: str
    short_name: str

    @property
    def private_key(self) -> bytes:
        return hashlib.sha256(b"chatko-lab-key-" + self.short_name.encode()).digest()

    @property
    def mqtt_user(self) -> str:
        return self.name  # each node has its own login (lab/mosquitto/start.sh)

    @property
    def mqtt_password(self) -> str:
        return f"chatko-lab-{self.name}"


NODES = (
    LabNode(name="hub", port=4403, long_name="chatko hub", short_name="HUB"),
    LabNode(name="radio", port=4404, long_name="Lab radio", short_name="RAD"),
    LabNode(name="radio2", port=4405, long_name="Lab radio 2", short_name="RD2"),
)


def settings_messages(iface: TCPInterface, node: LabNode) -> list[admin_pb2.AdminMessage]:
    """The admin messages that bring the node to the wanted settings, in order; empty if none."""
    local = iface.localNode
    messages: list[admin_pb2.AdminMessage] = []

    user = iface.getMyUser() or {}
    if user.get("longName") != node.long_name or user.get("shortName") != node.short_name:
        m = admin_pb2.AdminMessage()
        m.set_owner.long_name = node.long_name
        m.set_owner.short_name = node.short_name
        messages.append(m)

    lora = config_pb2.Config.LoRaConfig()
    lora.CopyFrom(local.localConfig.lora)
    if lora.region != REGION or not lora.config_ok_to_mqtt or lora.ignore_mqtt:
        region_was_unset = lora.region != REGION
        lora.region = REGION
        lora.config_ok_to_mqtt = True
        lora.ignore_mqtt = False
        m = admin_pb2.AdminMessage()
        m.set_config.lora.CopyFrom(lora)
        messages.append(m)
        if region_was_unset:
            # Setting a duty-cycle region (EU_868) for the first time makes the firmware turn
            # ignore_mqtt on, which drops every packet that came through MQTT. Send it again.
            messages.append(m)

    # After the region: the firmware derives the public key only when a region is set.
    security = config_pb2.Config.SecurityConfig()
    security.CopyFrom(local.localConfig.security)
    if security.private_key != node.private_key:
        security.private_key = node.private_key
        security.public_key = b""  # the firmware derives it from the private key
        m = admin_pb2.AdminMessage()
        m.set_config.security.CopyFrom(security)
        messages.append(m)

    mqtt = local.moduleConfig.mqtt
    wanted_mqtt = (
        mqtt.enabled
        and mqtt.address == MQTT_ADDRESS
        and mqtt.root == MQTT_ROOT
        and mqtt.encryption_enabled
        and not mqtt.json_enabled
        and not mqtt.tls_enabled
        and mqtt.username == node.mqtt_user
        and mqtt.password == node.mqtt_password
    )
    if not wanted_mqtt:
        m = admin_pb2.AdminMessage()
        m.set_module_config.mqtt.CopyFrom(mqtt)
        new = m.set_module_config.mqtt
        new.enabled = True
        new.address = MQTT_ADDRESS
        new.root = MQTT_ROOT
        new.encryption_enabled = True
        new.json_enabled = False
        new.tls_enabled = False
        new.username = node.mqtt_user
        new.password = node.mqtt_password
        messages.append(m)

    primary = local.channels[0]
    if not (primary.settings.uplink_enabled and primary.settings.downlink_enabled):
        m = admin_pb2.AdminMessage()
        m.set_channel.CopyFrom(primary)
        m.set_channel.settings.uplink_enabled = True
        m.set_channel.settings.downlink_enabled = True
        messages.append(m)

    private = local.channels[PRIVATE_INDEX]
    wanted_private = (
        private.role == channel_pb2.Channel.Role.SECONDARY
        and private.settings.name == PRIVATE_CHANNEL
        and private.settings.psk == PRIVATE_PSK
        and private.settings.uplink_enabled
        and private.settings.downlink_enabled
    )
    if not wanted_private:
        m = admin_pb2.AdminMessage()
        m.set_channel.CopyFrom(private)
        m.set_channel.index = PRIVATE_INDEX
        m.set_channel.role = channel_pb2.Channel.Role.SECONDARY
        m.set_channel.settings.name = PRIVATE_CHANNEL
        m.set_channel.settings.psk = PRIVATE_PSK
        m.set_channel.settings.uplink_enabled = True
        m.set_channel.settings.downlink_enabled = True
        messages.append(m)

    return messages


def provision(node: LabNode) -> bool:
    """Bring one node to the wanted settings in one transaction. Returns True when it rebooted."""
    iface = connect(node.port)
    try:
        messages = settings_messages(iface, node)
        if not messages:
            return False
        begin = admin_pb2.AdminMessage(begin_edit_settings=True)
        commit = admin_pb2.AdminMessage(commit_edit_settings=True)
        for message in (begin, *messages, commit):
            send_admin(iface, message)
        return True
    finally:
        iface.close()


def exchange_contacts() -> None:
    """Give each node the other one's public key, unless it already has it as a favorite."""
    ifaces = [connect(node.port) for node in NODES]
    try:
        users = [own_user(iface) for iface in ifaces]
        for iface in ifaces:
            for other, user in zip(ifaces, users, strict=True):
                num = other.localNode.nodeNum
                if other is iface:
                    continue
                entry = iface.nodesByNum.get(num, {})
                known = entry.get("user", {}).get("publicKey")
                if known == base64.b64encode(user.public_key).decode() and entry.get("isFavorite"):
                    continue
                send_admin(iface, add_contact_message(num, user))
                print(f"{node_id(iface.localNode.nodeNum)}: added {node_id(num)} as a contact")
    finally:
        for iface in ifaces:
            iface.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", choices=[node.name for node in NODES], help="provision one node only")
    parser.add_argument("--no-contacts", action="store_true", help="leave key discovery to NodeInfo")
    args = parser.parse_args()

    rebooted = False
    for node in NODES:
        if args.only and node.name != args.only:
            continue
        changed = provision(node)
        rebooted = rebooted or changed
        print(
            f"{node.long_name} (:{node.port}): {'provisioned, rebooting' if changed else 'already provisioned'}"
        )
    if not args.no_contacts:
        if rebooted:
            time.sleep(REBOOT_WAIT)
        exchange_contacts()
    return 0


if __name__ == "__main__":
    sys.exit(main())
