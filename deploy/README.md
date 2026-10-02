# Server stack

Our Mosquitto (TLS on 8883 for our gateways) and the hub's virtual Meshtastic node, as in
design.md §11 and D30. chatko itself runs next to them (`uv run chatko run`) and reaches the node on
`127.0.0.1:4413`. S29 will turn this into the full production setup (chatko in a container, backups).

```bash
deploy/setup.sh <public host name or IP> [gateway names...]
```

```bash
docker compose -f deploy/docker-compose.yml up -d
```

`setup.sh` is idempotent. It makes the self-signed certificate (`config/mosquitto/tls/`), one
Mosquitto user per hub node and per gateway with an ACL limited to the root topic `msh/EU_433/#`,
and the secrets in `.env` (`MQTT_PASSWORD_<USER>`, `MESH_FAMILY_PSK`; `KYIV_PRIMARY_PSK` is the
Kyiv mesh's public key from spikes.md S2). Add a gateway by running it again with all the names.
Only 8883 is published; the node's API (4413) is on `127.0.0.1`.

## Settings for our physical gateway (S23)

`EU_433` region; "Ignore MQTT" off, "OK to MQTT" on; channel 0 `LongFast` with the Kyiv primary PSK
and channel 1 `family` with `MESH_FAMILY_PSK`, both with uplink and downlink on; MQTT enabled with
address `<host>:8883`, TLS on, the gateway's user and password from `.env`, root topic
`msh/EU_433`, encryption on, JSON off; Wi-Fi to the internet. Watch the broker with:

```bash
docker compose -f deploy/docker-compose.yml logs -f mosquitto
```
