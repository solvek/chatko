# /// script
# requires-python = ">=3.11"
# dependencies = ["meshtastic==2.7.11", "paho-mqtt>=2"]
# ///
"""Spike S2c: read-only listener on the Kyiv community broker (design.md §6.2).

Subscribes to a topic filter, prints every topic seen with counts and, for decodable
ServiceEnvelopes, the channel id, the port and the flags. Never publishes.
"""
import argparse
import collections
import os
import ssl
import time

import paho.mqtt.client as mqtt
from meshtastic.protobuf import mqtt_pb2, portnums_pb2

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="mqtt.meshtastic.kyiv.ua")
ap.add_argument("--port", type=int, default=1883)
ap.add_argument("--tls", action="store_true", help="use TLS (port 8883)")
ap.add_argument("--user", default=os.environ.get("KYIV_MQTT_USER"))
ap.add_argument("--password", default=os.environ.get("KYIV_MQTT_PASSWORD"))
ap.add_argument("--topic", default="#")
ap.add_argument("--seconds", type=int, default=60)
a = ap.parse_args()

topics: collections.Counter[str] = collections.Counter()
rows: list[str] = []


def on_connect(c, _u, _f, rc, _p=None):
    print("connect:", rc)
    c.subscribe(a.topic)


def on_subscribe(_c, _u, _m, codes, _p=None):
    print("subscribe result:", codes)


def on_message(_c, _u, msg):
    topics["/".join(msg.topic.split("/")[:4])] += 1
    env = mqtt_pb2.ServiceEnvelope()
    try:
        env.ParseFromString(msg.payload)
    except Exception:
        return
    p = env.packet
    if len(rows) < 40:
        kind = "enc" if p.encrypted else portnums_pb2.PortNum.Name(p.decoded.portnum)
        rows.append(f"{msg.topic} ch={env.channel_id} gw={env.gateway_id} from={p.from_:08x} "
                    f"to={p.to:08x} {kind} pki={p.pki_encrypted} via_mqtt={p.via_mqtt} hop={p.hop_limit}")


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
if a.user:
    c.username_pw_set(a.user, a.password)
if a.tls:
    c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
c.on_connect, c.on_message, c.on_subscribe = on_connect, on_message, on_subscribe
c.connect(a.host, 8883 if a.tls and a.port == 1883 else a.port)
c.loop_start()
time.sleep(a.seconds)
c.loop_stop()
print("\ntopic prefixes:")
for t, n in topics.most_common(30):
    print(f"{n:6} {t}")
print("\nsample envelopes:")
print("\n".join(rows))
