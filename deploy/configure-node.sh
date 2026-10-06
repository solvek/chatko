#!/usr/bin/env bash
# Configure a physical Meshtastic node (a member's radio, or the owner's) for chatko over USB.
#
#   CHATKO_PSK=<base64, 32 bytes> deploy/configure-node.sh [--port /dev/ttyACM0]
#
# What it sets (S23, D63, spikes.md):
#   channel 0  the hub's private channel (default name `chatko`, key CHATKO_PSK): it MUST be the same
#              name and key as channel 0 of the hub's node, or the ACKs of direct messages are lost
#   channel 1  the Kyiv community's `LongFast` (public key, from https://meshtastic.kyiv.ua/join)
#   channel 2  the community's `KyivUA` (public key, same QR code)
#   LoRa       region EU_433, LongFast preset, frequency slot 1 (kept explicit: the slot must not follow the
#              primary channel's name), Ignore MQTT off, OK to MQTT on
# and, only when MQTT_ADDRESS is set, the node's MQTT client (a node with internet, as a gateway):
#   MQTT_ADDRESS MQTT_USERNAME MQTT_PASSWORD MQTT_ROOT   encryption on (a node with it on drops decoded
#   envelopes and the hub's traffic is encrypted), proxy to client on (a node without Wi-Fi: the phone
#   app relays), map reporting off.
# Optional: ADC_MULTIPLIER (battery voltage override), REGION, SLOT, CHATKO_CHANNEL.
#
# The key goes on the command line (`ps` shows it to other users of this machine) and into nothing else.
# Never commit it. Needs the `meshtastic` CLI (`pipx install meshtastic`); the node reboots after most
# steps, so the script waits between them.
set -euo pipefail

PORT=/dev/ttyACM0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

: "${CHATKO_PSK:?set CHATKO_PSK to the private channel key of the hub (base64, 32 bytes)}"
CHANNEL="${CHATKO_CHANNEL:-chatko}"
REGION="${REGION:-EU_433}"
SLOT="${SLOT:-1}"
# The Kyiv community's public keys (they are in the QR code of https://meshtastic.kyiv.ua/join).
KYIV_LONGFAST_PSK="${KYIV_LONGFAST_PSK:-XOLPZHTWzHgykJxZ3pnj10mMJdr8glgblsNGLUIvd1w=}"
KYIVUA_PSK="${KYIVUA_PSK:-cmF4UzVWbnZWQ0xxZlFyZXBSb2xhaHRNSkI1bFhabzU=}"
WAIT="${WAIT:-12}"

m() {  # one CLI call, then wait for the node's reboot
  meshtastic --port "$PORT" "$@"
  sleep "$WAIT"
}

echo "== LoRa: region $REGION, slot $SLOT"
m --set lora.region "$REGION"
m --set lora.use_preset true --set lora.modem_preset LONG_FAST --set lora.channel_num "$SLOT"
# Setting a duty-cycle region for the first time makes the firmware turn Ignore MQTT on: set it again.
m --set lora.ignore_mqtt false --set lora.config_ok_to_mqtt true

echo "== channels: 0 $CHANNEL (private), 1 LongFast, 2 KyivUA"
m --ch-index 0 --ch-set name "$CHANNEL" --ch-set psk "base64:$CHATKO_PSK" \
  --ch-set uplink_enabled true --ch-set downlink_enabled true
m --ch-index 1 --ch-enable --ch-set name LongFast --ch-set psk "base64:$KYIV_LONGFAST_PSK" \
  --ch-set uplink_enabled true --ch-set downlink_enabled true
m --ch-index 2 --ch-enable --ch-set name KyivUA --ch-set psk "base64:$KYIVUA_PSK" \
  --ch-set uplink_enabled true --ch-set downlink_enabled true

if [ -n "${MQTT_ADDRESS:-}" ]; then
  : "${MQTT_USERNAME:?set MQTT_USERNAME}" "${MQTT_PASSWORD:?set MQTT_PASSWORD}" "${MQTT_ROOT:?set MQTT_ROOT}"
  echo "== MQTT client: $MQTT_ADDRESS, root $MQTT_ROOT"
  m --set mqtt.enabled true --set mqtt.address "$MQTT_ADDRESS" --set mqtt.username "$MQTT_USERNAME" \
    --set mqtt.password "$MQTT_PASSWORD" --set mqtt.root "$MQTT_ROOT" --set mqtt.encryption_enabled true \
    --set mqtt.json_enabled false --set mqtt.tls_enabled false --set mqtt.proxy_to_client_enabled true \
    --set mqtt.map_reporting_enabled false
fi

if [ -n "${ADC_MULTIPLIER:-}" ]; then
  echo "== battery voltage multiplier $ADC_MULTIPLIER"
  m --set power.adc_multiplier_override "$ADC_MULTIPLIER"
fi

echo "== result"
meshtastic --port "$PORT" --info | grep -E "Index [0-9]|myNodeNum|firmwareVersion" | sed -E 's/"psk": "[^"]*", //'
meshtastic --port "$PORT" --get lora.region --get lora.channel_num --get lora.ignore_mqtt
