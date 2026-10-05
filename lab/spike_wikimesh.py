# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0"]
# ///
"""Spike S23 (D62): can a virtual node read the community broker with encryption off?

Provisions the lab's `wikimesh` probe (:4406) for the community broker named in the environment
(`WIKIMESH_MQTT_HOST`, `_USER`, `_PASSWORD`; `.env`): region EU_433, the Kyiv channel from the
community's QR, MQTT root `kyiv`, encryption **off**. With `--listen` (the default) the node only
reads: uplink is off on its channels, so it publishes nothing but its own presence is not announced
by packets. It then prints what the node learned from the broker.

    docker compose -f lab/docker-compose.yml --profile wikimesh up -d wikimesh
    set -a; . ./.env; set +a; uv run lab/spike_wikimesh.py [--seconds 120]
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time

from labkit import connect, send_admin
from meshtastic.protobuf import admin_pb2, apponly_pb2, channel_pb2, config_pb2

PORT = 4406
# The Kyiv community's channel URL (https://meshtastic.kyiv.ua/join): LongFast with its own PSK.
KYIV_URL = (
    "CjQSIFziz2R01sx4MpCcWd6Z49dJjCXa_IJYG5bDRi1CL3dcGghMb25nRmFzdCgBMAE6AgggCjISIHJheFM1Vm52"
    "VkNMcWZRcmVwUm9sYWh0TUpCNWxYWm81GgZLeWl2VUEoATABOgIIIBIOCAE4DkAFSAFQClgBaAE"
)
REGION = config_pb2.Config.LoRaConfig.RegionCode.EU_433


def kyiv_channels() -> list[channel_pb2.ChannelSettings]:
    raw = base64.urlsafe_b64decode(KYIV_URL + "=" * (-len(KYIV_URL) % 4))
    channel_set = apponly_pb2.ChannelSet()
    channel_set.ParseFromString(raw)
    return list(channel_set.settings)


def settings_messages(
    uplink: bool, test_channel: tuple[str, bytes] | None = None
) -> list[admin_pb2.AdminMessage]:
    host, user, password = (os.environ[f"WIKIMESH_MQTT_{k}"] for k in ("HOST", "USER", "PASSWORD"))
    messages: list[admin_pb2.AdminMessage] = []

    m = admin_pb2.AdminMessage()
    m.set_owner.long_name, m.set_owner.short_name = "chatko probe", "PRB"
    messages.append(m)

    m = admin_pb2.AdminMessage()
    lora = m.set_config.lora
    lora.region = REGION
    lora.use_preset = True
    lora.modem_preset = config_pb2.Config.LoRaConfig.ModemPreset.LONG_FAST
    lora.ignore_mqtt = False
    lora.config_ok_to_mqtt = True
    lora.hop_limit = 5
    messages.append(m)

    m = admin_pb2.AdminMessage()
    mqtt = m.set_module_config.mqtt
    mqtt.enabled = True
    mqtt.address = host
    mqtt.username = user
    mqtt.password = password
    mqtt.root = "kyiv"
    mqtt.encryption_enabled = False
    mqtt.json_enabled = False
    mqtt.tls_enabled = False
    messages.append(m)

    for index, settings in enumerate(kyiv_channels()):
        m = admin_pb2.AdminMessage()
        m.set_channel.index = index
        m.set_channel.role = (
            channel_pb2.Channel.Role.PRIMARY if index == 0 else channel_pb2.Channel.Role.SECONDARY
        )
        m.set_channel.settings.CopyFrom(settings)
        m.set_channel.settings.uplink_enabled = uplink
        m.set_channel.settings.downlink_enabled = True
        messages.append(m)
    if test_channel:  # a private channel of our own: the community's nodes do not subscribe to its name
        name, psk = test_channel
        m = admin_pb2.AdminMessage()
        m.set_channel.index = 2
        m.set_channel.role = channel_pb2.Channel.Role.SECONDARY
        m.set_channel.settings.name = name
        m.set_channel.settings.psk = psk
        m.set_channel.settings.uplink_enabled = uplink
        m.set_channel.settings.downlink_enabled = True
        messages.append(m)
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=int, default=120, help="how long to listen")
    parser.add_argument("--uplink", action="store_true", help="let the node publish (off by default)")
    parser.add_argument("--test-channel", metavar="NAME:PSK_BASE64", help="add a private channel at index 2")
    args = parser.parse_args()

    test_channel = None
    if args.test_channel:
        name, _, psk = args.test_channel.partition(":")
        test_channel = (name, base64.b64decode(psk))

    iface = connect(PORT)
    try:
        begin = admin_pb2.AdminMessage(begin_edit_settings=True)
        commit = admin_pb2.AdminMessage(commit_edit_settings=True)
        for message in (begin, *settings_messages(args.uplink, test_channel), commit):
            send_admin(iface, message)
    finally:
        iface.close()
    print("provisioned; the node reboots, then listens for", args.seconds, "s")
    time.sleep(15)

    iface = connect(PORT)
    try:
        time.sleep(args.seconds)
        nodes = iface.nodesByNum
        print(f"nodes the probe learned from the broker: {len(nodes) - 1}")
        for num, entry in list(nodes.items())[:15]:
            user = entry.get("user", {})
            print(f"  !{num:08x}  {user.get('longName', '?')}  via_mqtt={entry.get('viaMqtt', False)}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
