# Local Meshtastic lab

A Mosquitto broker and two virtual Meshtastic nodes (`meshtasticd` with a simulated radio), so the
Meshtastic extension can be developed and tested without hardware (design.md §11, spike S2).

| Service | Plays | Node id | TCP API on the host |
|---|---|---|---|
| `mosquitto` | the MQTT broker | — | `127.0.0.1:1883` |
| `hub` | chatko's virtual node | `!c4a7b001` | `127.0.0.1:4403` |
| `radio` | a member's radio node | `!c4a7b002` | `127.0.0.1:4404` |

The nodes reach each other **only through MQTT**: the simulated radio does not transmit, and UDP
between nodes is off. All ports listen on `127.0.0.1` only; the `meshtasticd` API has no
authentication.

## Use

Needs Docker with Compose and [`uv`](https://docs.astral.sh/uv/). The scripts declare their own
dependencies (`meshtastic`, `paho-mqtt`), so `uv run` needs no project setup.

```bash
docker compose -f lab/docker-compose.yml up -d
```

```bash
uv run lab/provision.py
```

```bash
uv run lab/spike_channel.py
```

`provision.py` sets names, region `EU_868` (with `ignore_mqtt` off), the MQTT client (broker
`mosquitto`, root `msh/lab`, encrypted, uplink and downlink on both channels) and the private channel
`Family` at index 1. It is idempotent; the first run reboots each node once. `spike_channel.py` sends a
text on `Family` radio → hub and hub → radio, prints the MQTT envelopes and the received packet fields,
and exits non-zero if either direction fails.

Stop the lab with `docker compose -f lab/docker-compose.yml down`; add `-v` to wipe the nodes' state
(node keys, settings, node database).

## Files

- `docker-compose.yml`: the three services. `MESHTASTICD_TAG` overrides the pinned image tag.
- `meshtasticd/{hub,radio}.yaml`: the `meshtasticd` config. `Lora: Module: sim` selects the simulated
  radio; `General: MACAddress` fixes the node id (its last 4 bytes). Set `Logging: LogLevel: debug` to
  see why a packet is dropped.
- `mosquitto/mosquitto.conf`: an anonymous listener, fine for the lab only.
- `provision.py`, `spike_channel.py`: spike scripts, not chatko code. The findings are in
  [docs/spikes.md](../docs/spikes.md) (S2).

## Pitfalls found in the spike

- Setting a region with a duty-cycle limit (`EU_868`) for the first time makes the firmware turn
  `lora.ignore_mqtt` on. The node then drops (and does not relay) every packet that crossed MQTT.
  `provision.py` writes the LoRa config a second time to turn it off.
- Admin messages sent back to back are partly lost; `provision.py` pauses 1 s between them.
- A node reboot (after settings change) ends the `meshtasticd` process; the compose restart policy
  brings it back. Keep `restart: unless-stopped`.
- `meshtasticd` serves one API client at a time: a new TCP connection closes the previous one. Running
  the `meshtastic` CLI against a node kicks off whatever else is connected to it.
