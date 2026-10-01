# Local lab

Two independent setups: the Meshtastic lab (below) and briar-headless ([Briar](#briar-headless)).

## Meshtastic

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

The broker is set up like production's (design.md §11, D30, D46): no anonymous clients, a user per
node, and an ACL that keeps every user to the lab's root topic `msh/lab/#`. The password of user X
is `chatko-lab-X`, a lab value for a broker on `127.0.0.1` only, never for a real one.

| User | Used by |
|---|---|
| `hub` | the hub node's MQTT client |
| `radio` | the radio node's MQTT client |
| `lab` | the spike scripts and anyone watching the broker (`mosquitto_sub -u lab -P chatko-lab-lab -t 'msh/lab/#' -v`) |

### Use

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

```bash
uv run lab/spike_dm.py
```

`provision.py` sets names, region `EU_868` (with `ignore_mqtt` off), a private key, the MQTT client
(broker `mosquitto` with the node's own login, root `msh/lab`, encrypted, uplink and downlink on
both channels) and the private channel `Family` at index 1, in one settings transaction per node, waiting for the node's response to
each admin message. Then it gives each node the other one's public key as a contact, so direct
messages work at once. It is idempotent; a run that changes settings reboots the node once.
`--only hub|radio` provisions one node (e.g. after wiping its volume), `--no-contacts` leaves key
discovery to NodeInfo.

`spike_channel.py` sends a text on `Family` radio → hub and hub → radio; `spike_dm.py` sends direct
messages both ways and records ACKs and NAKs. Both print the MQTT envelopes and the received packet
fields, and exit non-zero if a direction fails.

Stop the lab with `docker compose -f lab/docker-compose.yml down`; add `-v` to wipe the nodes' state
(settings, node database; `provision.py` gives the nodes their lab keys back).

### Files

- `docker-compose.yml`: the three services. `MESHTASTICD_TAG` overrides the pinned image tag.
- `meshtasticd/{hub,radio}.yaml`: the `meshtasticd` config. `Lora: Module: sim` selects the simulated
  radio; `General: MACAddress` fixes the node id (its last 4 bytes). Set `Logging: LogLevel: debug` to
  see why a packet is dropped.
- `mosquitto/mosquitto.conf`, `mosquitto/acl`, `mosquitto/start.sh`: the broker's settings, the
  ACL, and the start script that makes the password file of the lab users.
- `provision.py`: provisions both nodes (see above).
- `spike_channel.py`, `spike_dm.py`, `spike_keys.py`, `spike_admin.py`: spike scripts, not chatko
  code. `spike_dm.py --offline` also stops the radio to see the retries; `spike_keys.py show|learn|
  contact` shows the node databases, forgets and re-learns keys, or adds a key with `add_contact`;
  `spike_admin.py` sends admin messages back to back and one at a time.
- `labkit.py`: helpers the scripts share (connecting, recording packets and MQTT envelopes,
  waiting for admin responses).

The findings are in [docs/spikes.md](../docs/spikes.md) (S2).

### The adapter's lab tests

`tests/integration/test_meshtastic_lab.py` runs chatko's Meshtastic adapter (`MeshNode` over
`LibraryMeshApi`) against both nodes: provisioning with the reboot it causes, channel texts and
direct messages both ways with their ACKs, the NAKs of a node without a key and of a radio that is
away, and a node restart. They are deselected by default; with the lab up:

```bash
uv run pytest -m lab tests/integration/test_meshtastic_lab.py
```

They take about 80 s, reboot the hub node twice and stop and restart containers. They use the
settings of `provision.py` (with the primary channel named `LongFast`, which changes nothing on
the air), so the two do not undo each other.

### Pitfalls found in the spike

- Setting a region with a duty-cycle limit (`EU_868`) for the first time makes the firmware turn
  `lora.ignore_mqtt` on. The node then drops (and does not relay) every packet that crossed MQTT.
  `provision.py` writes the LoRa config a second time to turn it off.
- The node keeps at most 4 packets addressed to itself in a queue and drops the oldest, so admin
  messages sent back to back are partly lost. `provision.py` waits for each message's response.
- A node reboot (after settings change) ends the `meshtasticd` process; the compose restart policy
  brings it back. Keep `restart: unless-stopped`.
- `meshtasticd` serves one API client at a time: a new TCP connection closes the previous one. Running
  the `meshtastic` CLI against a node kicks off whatever else is connected to it.
- Nodes pin keys. After wiping one node's volume (`docker compose rm -sf radio`, `docker volume rm
  chatko-lab_radio-data`), provision it with `--only radio`: the lab private key gives it its old
  public key back. Without it, the other node keeps the old key; `spike_keys.py contact hub` (or
  `contact radio`) replaces it.
- Keys learned from NodeInfo in a node's first minute are not saved, and `docker stop` kills
  `meshtasticd` after 10 s without saving. The contacts that `provision.py` adds are saved at once.
- A node sends at most one text per 2 s from its API client and drops the rest without telling the
  client. Scripts pause before each text.
- While a node's container restarts, Docker's port proxy still accepts a connection on 4403 or
  4404 and closes it at once: a client sees a broken pipe and has to try again (S19).

## briar-headless

`briar/` builds upstream briar-headless (tag `release-1.5.21`, JDK 17) and runs it with one Briar
account, its REST and WebSocket API on `127.0.0.1:7000` and its state in the volume `briar-data`
(spike S3). It needs internet (Tor) and a phone with Briar for the other side.

Put the secrets into `lab/briar/.env` (git-ignored):

```bash
printf 'BRIAR_PASSWORD=%s\nBRIAR_AUTH_TOKEN=%s\n' "$(openssl rand -base64 18)" "$(openssl rand -base64 32)" > lab/briar/.env
```

```bash
docker compose -f lab/briar/docker-compose.yml up -d --build
```

The first start creates the account (nickname `chatko-lab`, or `BRIAR_NICKNAME`); later starts sign
in with the same password. `spike_briar.py` is the spike client; it reads the token from the
container:

```bash
uv run lab/spike_briar.py link
```

```bash
uv run lab/spike_briar.py add 'briar://…' --alias phone
```

```bash
uv run lab/spike_briar.py watch
```

```bash
uv run lab/spike_briar.py send 1 "hello"
```

`pending`, `contacts` and `messages <contactId>` list the rest. Both sides must add the other's link;
the contact appears within seconds after that. The first build takes about 2 min. `down -v` wipes the
account, after which the phone has to add the new link again.
