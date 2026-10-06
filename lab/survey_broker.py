# /// script
# requires-python = ">=3.12"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt==2.1.0", "cryptography"]
# ///
"""Test T1 (docs/meshtastic-testing.md): a passive survey of a Meshtastic broker.

Subscribes to `<root>/#` with the login in the environment (`WIKIMESH_MQTT_USER`, `_PASSWORD`; host
`WIKIMESH_MQTT_HOST`, default `mqtt.wikimesh.in.ua`; root `WIKIMESH_MQTT_ROOT`, default `kyiv`) and
publishes nothing. Each envelope goes to a JSON-lines log (topic, gateway, channel, sender, whether it
was published decoded or encrypted, and for an encrypted one the key that decrypts it); at the end it
prints counts. A gateway that publishes encrypted envelopes has MQTT encryption on, which decides
whether it uplinks others' direct messages (rule 2). The log has no text, only its length.

    set -a; . ./.env; set +a; uv run lab/survey_broker.py [seconds=300] [log=survey.jsonl]
"""

from __future__ import annotations

import base64
import collections
import json
import os
import struct
import sys
import time

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
LOG = sys.argv[2] if len(sys.argv) > 2 else "survey.jsonl"
HOST = os.environ.get("WIKIMESH_MQTT_HOST", "mqtt.wikimesh.in.ua")
ROOT = os.environ.get("WIKIMESH_MQTT_ROOT", "kyiv")
DEFAULT = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
KEYS = {
    "LongFast/default": ("LongFast", DEFAULT),
    "LongFast/kyiv": ("LongFast", base64.b64decode("XOLPZHTWzHgykJxZ3pnj10mMJdr8glgblsNGLUIvd1w=")),
    "KyivUA": ("KyivUA", base64.b64decode("cmF4UzVWbnZWQ0xxZlFyZXBSb2xhaHRNSkI1bFhabzU=")),
}


def xor(b: bytes) -> int:
    h = 0
    for x in b:
        h ^= x
    return h


HASHES = {k: xor(n.encode()) ^ xor(p) for k, (n, p) in KEYS.items()}


def decrypt(p: mesh_pb2.MeshPacket) -> tuple[str, mesh_pb2.Data] | None:
    nonce = struct.pack("<QI", p.id, getattr(p, "from")) + b"\0" * 4
    for name, (_, key) in KEYS.items():
        if HASHES[name] != p.channel:
            continue
        d = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
        data = mesh_pb2.Data()
        try:
            data.ParseFromString(d.update(p.encrypted) + d.finalize())
        except Exception:
            continue
        if data.portnum:
            return name, data
    return None


stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
log = open(LOG, "a")


def on_message(_c, _u, msg):  # type: ignore[no-untyped-def]
    t = msg.topic
    parts = t.split("/")
    rec: dict = {"t": round(time.time(), 1), "topic": t}
    if len(parts) >= 3 and parts[2] == "e":
        env = mqtt_pb2.ServiceEnvelope()
        try:
            env.ParseFromString(msg.payload)
        except Exception:
            stats["bad"][t] += 1
            return
        p = env.packet
        rec |= {
            "ch": env.channel_id, "gw": env.gateway_id, "from": f"!{getattr(p, 'from'):08x}",
            "to": f"!{p.to:08x}", "id": p.id, "hop_start": p.hop_start, "hop_limit": p.hop_limit,
            "via_mqtt": p.via_mqtt, "chash": p.channel, "pki": p.pki_encrypted,
        }
        if p.HasField("decoded"):
            rec["state"] = "decoded"
            rec["port"] = portnums_pb2.PortNum.Name(p.decoded.portnum)
            if p.decoded.portnum == portnums_pb2.TEXT_MESSAGE_APP:
                rec["text_len"] = len(p.decoded.payload)
        else:
            r = decrypt(p) if env.channel_id != "PKI" else None
            if r:
                rec["state"] = f"encrypted,decrypted:{r[0]}"
                rec["port"] = portnums_pb2.PortNum.Name(r[1].portnum)
            else:
                rec["state"] = "encrypted"
        stats["channel"][env.channel_id] += 1
        stats["state"][f"{env.channel_id}:{rec['state']}"] += 1
        stats["gateway"][env.gateway_id] += 1
        stats["sender"][rec["from"]] += 1
        stats["gw_ch"][f"{env.gateway_id} {env.channel_id}"] += 1
        stats["port"][rec.get("port", "?")] += 1
        if rec["from"] != env.gateway_id:
            stats["heard_by_gw"][f"{env.gateway_id} <- {rec['from']}"] += 1
    else:
        kind = parts[2] if len(parts) > 2 else "?"
        stats["other"][kind] += 1
        if kind == "map":
            try:
                env = mqtt_pb2.ServiceEnvelope()
                env.ParseFromString(msg.payload)
                mr = mqtt_pb2.MapReport()
                mr.ParseFromString(env.packet.decoded.payload)
                rec["map"] = {"from": f"!{getattr(env.packet, 'from'):08x}", "name": mr.long_name,
                              "fw": mr.firmware_version, "region": mr.region,
                              "preset": mr.modem_preset, "default_ch": mr.has_default_channel}
            except Exception:
                pass
    log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.flush()


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"chatko-survey-{os.getpid()}")
c.username_pw_set(os.environ["WIKIMESH_MQTT_USER"], os.environ["WIKIMESH_MQTT_PASSWORD"])
c.on_message = on_message
c.on_connect = lambda c, u, f, rc, p=None: (print("connected", rc, flush=True), c.subscribe(f"{ROOT}/#"))
c.connect(HOST, 1883, 60)
c.loop_start()
time.sleep(SECONDS)
c.loop_stop()
print("hashes", HASHES)
for k, v in stats.items():
    print(f"== {k}")
    for name, n in v.most_common(40):
        print(f"  {n:5d}  {name}")
