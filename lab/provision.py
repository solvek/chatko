# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11"]
# ///
"""Provision the lab's virtual nodes over their TCP API (spike S2).

Sets the owner names, the region, a private channel and the MQTT client of each node, so that the
nodes reach each other through the lab's Mosquitto. Idempotent: a node whose settings already match
is left alone, so it is not rebooted again.

    uv run lab/provision.py
"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass

from meshtastic.protobuf import channel_pb2, config_pb2
from meshtastic.tcp_interface import TCPInterface

# Lab values only. This PSK is derived from a public string; never use it on a real mesh.
PRIVATE_CHANNEL = "Family"
PRIVATE_PSK = hashlib.sha256(b"chatko-lab-family").digest()
PRIVATE_INDEX = 1
MQTT_ADDRESS = "mosquitto"  # the broker's name inside the compose network
MQTT_ROOT = "msh/lab"
ADMIN_PAUSE = 1.0  # seconds between admin messages; the node drops some when they come back to back
REGION = config_pb2.Config.LoRaConfig.RegionCode.EU_868


@dataclass(frozen=True)
class LabNode:
    port: int
    long_name: str
    short_name: str


NODES = (
    LabNode(port=4403, long_name="chatko hub", short_name="HUB"),
    LabNode(port=4404, long_name="Lab radio", short_name="RAD"),
)


def connect(port: int, attempts: int = 30) -> TCPInterface:
    """Connect to a node, waiting while it (re)starts."""
    for attempt in range(attempts):
        try:
            iface = TCPInterface(hostname="127.0.0.1", portNumber=port)
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


def provision(node: LabNode) -> bool:
    """Bring one node to the wanted settings. Returns True when something was written."""
    iface = connect(node.port)
    try:
        local = iface.localNode
        changed = False

        user = iface.getMyUser() or {}
        if user.get("longName") != node.long_name or user.get("shortName") != node.short_name:
            local.setOwner(long_name=node.long_name, short_name=node.short_name)
            time.sleep(ADMIN_PAUSE)
            changed = True

        lora = local.localConfig.lora
        mqtt = local.moduleConfig.mqtt
        primary = local.channels[0]
        private = local.channels[PRIVATE_INDEX]

        wanted_lora = lora.region == REGION and lora.config_ok_to_mqtt and not lora.ignore_mqtt
        wanted_mqtt = (
            mqtt.enabled
            and mqtt.address == MQTT_ADDRESS
            and mqtt.root == MQTT_ROOT
            and mqtt.encryption_enabled
            and not mqtt.json_enabled
            and not mqtt.tls_enabled
            and mqtt.username == ""
            and mqtt.password == ""
        )
        wanted_primary = primary.settings.uplink_enabled and primary.settings.downlink_enabled
        wanted_private = (
            private.role == channel_pb2.Channel.Role.SECONDARY
            and private.settings.name == PRIVATE_CHANNEL
            and private.settings.psk == PRIVATE_PSK
            and private.settings.uplink_enabled
            and private.settings.downlink_enabled
        )
        if wanted_lora and wanted_mqtt and wanted_primary and wanted_private:
            return changed

        local.beginSettingsTransaction()
        time.sleep(ADMIN_PAUSE)
        if not wanted_lora:
            region_was_unset = lora.region != REGION
            lora.region = REGION
            lora.config_ok_to_mqtt = True
            lora.ignore_mqtt = False
            local.writeConfig("lora")
            time.sleep(ADMIN_PAUSE)
            if region_was_unset:
                # Setting a duty-cycle region (EU_868) for the first time makes the firmware turn
                # ignore_mqtt on, which drops every packet that came through MQTT. Write it again.
                local.writeConfig("lora")
                time.sleep(ADMIN_PAUSE)
        if not wanted_mqtt:
            mqtt.enabled = True
            mqtt.address = MQTT_ADDRESS
            mqtt.root = MQTT_ROOT
            mqtt.encryption_enabled = True
            mqtt.json_enabled = False
            mqtt.tls_enabled = False
            mqtt.username = ""
            mqtt.password = ""
            local.writeConfig("mqtt")
            time.sleep(ADMIN_PAUSE)
        if not wanted_primary:
            primary.settings.uplink_enabled = True
            primary.settings.downlink_enabled = True
            local.writeChannel(0)
            time.sleep(ADMIN_PAUSE)
        if not wanted_private:
            private.role = channel_pb2.Channel.Role.SECONDARY
            private.settings.name = PRIVATE_CHANNEL
            private.settings.psk = PRIVATE_PSK
            private.settings.uplink_enabled = True
            private.settings.downlink_enabled = True
            local.writeChannel(PRIVATE_INDEX)
            time.sleep(ADMIN_PAUSE)
        local.commitSettingsTransaction()
        time.sleep(ADMIN_PAUSE)
        time.sleep(2)  # let the node persist before we drop the connection
        return True
    finally:
        iface.close()


def main() -> int:
    for node in NODES:
        changed = provision(node)
        print(f"{node.long_name} (:{node.port}): {'provisioned' if changed else 'already provisioned'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
