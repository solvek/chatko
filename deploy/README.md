# Server stack

The production stack of design.md §11 (D30, D53, D57, D58): the hub in a container (`chatko/`), our
Mosquitto (TLS on 8883 for gateways of our own, closed to the internet by default), the hub's virtual Meshtastic node and its briar-headless.
The guide for a server, with backups and operations, is [docs/deployment.md](../docs/deployment.md);
this file describes the parts.

```bash
deploy/setup.sh <public host name or IP> [gateway names...]
```

```bash
docker compose -f deploy/docker-compose.yml up -d
```

`setup.sh` is idempotent. It makes the self-signed certificate (`config/mosquitto/tls/`), one
Mosquitto user per hub node and per gateway with an ACL limited to the root topic `msh/EU_433/#`,
and the secrets in `.env` (`MQTT_PASSWORD_<USER>`, `MESH_FAMILY_PSK`, `MESH_KYIV_PRIVATE_KEY`, the
hub node's identity; `KYIV_PRIMARY_PSK` is the Kyiv mesh's public key from spikes.md S2;
`BRIAR_PASSWORD`, `BRIAR_AUTH_TOKEN`), the user ids the hub's container runs as (`CHATKO_UID`,
`CHATKO_GID`: yours, not root), and links `.env` as `deploy/.env`, where Compose reads it. Add a
gateway by running it again with all the names. 8883 is published on `127.0.0.1` only: for a gateway of
our own set `MQTT_TLS_BIND=0.0.0.0` in `.env`. The node's API (4413) and briar-headless's (7000) are on
`127.0.0.1`. `MQTT_TLS_PORT`, `MESH_API_PORT` and `BRIAR_API_PORT` in
`.env` move the host ports (to run a second copy for a restore test).

## chatko

`chatko/Dockerfile` builds the hub for amd64 and arm64 from the repository (the build context is the
repository root, and `.dockerignore` keeps `.env`, `config/` and `data/` out of it). The container
runs as your user, with `config/` read-only at `/app/config`, its database in `data/chatko`, and only
the variables that `chatko.yaml` names: the `environment` list of the service. It stops on SIGTERM
within a couple of seconds.

## Backups

`backup.sh` (daily with the timer in `systemd/`) and `restore.sh`; see docs/deployment.md §6. Tests:
`tests/deploy`.

## briar-headless

`briar/` is the image of our briar-headless fork (upstream plus the private-group API, D29, D51,
D53), for amd64 and arm64; `lab/briar/` uses it too. Its build needs BuildKit (`docker buildx`;
Ubuntu's package is `docker-buildx`) and the fork's source as the build context `briar`: by default
a checkout of the branch `1664-private-group-api` at `../briar`, next to this repository, or a path
or Git URL in `BRIAR_SRC` (`https://…/briar.git#1664-private-group-api`). The build runs Gradle on
the build machine for both architectures (about 1 min with a warm cache), so an arm64 image can be
built on an amd64 machine; only the runtime stage runs under QEMU there, which needs the arm64
binfmt handler once per boot:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

```bash
docker buildx build --build-context briar=../briar --platform linux/arm64 --load -t chatko/briar-headless:1.5.21 deploy/briar
```

On the server itself, `docker compose -f deploy/docker-compose.yml build briar` builds for its own
architecture; `docker save chatko/briar-headless:1.5.21 | ssh <server> docker load` copies an image
built elsewhere.

The first start creates the account (nickname `chatko`, or `BRIAR_NICKNAME`) in `data/briar`; later
starts sign in with `BRIAR_PASSWORD`, which the data is useless without, so keep it with the backups'
secrets (D28). `BRIAR_AUTH_TOKEN` is the API token for chatko and `briarctl`. A wrong password makes
the container exit and restart in a loop; the log shows it. The container is healthy once the API
answers; Tor connects within a minute after that (`INFO: Bootstrapped` in the log).

### Joining the hub to a Briar group (`briarctl`)

The hub's contacts and groups are managed with `briarctl` (design.md §7.5, D55), from the checkout
that chatko runs in, with the token of `.env`:

```bash
set -a; . ./.env; set +a
```

```bash
uv run briarctl link
```

Give that link to each person; they add it in Briar ("Add contact at a distance"), and the hub
adds theirs with `uv run briarctl contact add 'briar://…' --alias Nat`. Both sides must do it.
Then a person creates the group in the app and invites the hub, and:

```bash
uv run briarctl invitation list
```

```bash
uv run briarctl invitation accept '<group id>'
```

```bash
uv run briarctl group reveal '<group id>' Nat Ada
```

The id goes into `chatko.yaml` as the Briar site's `group`. If a person cannot create the group,
`uv run briarctl group create Family` makes one with the hub as its creator, and `group invite` adds
contacts to it (D52). Every command also takes `--json`.


## The Kyiv broker (v1, D58)

The hub's node connects to `mqtt.meshtastic.kyiv.ua:1883` (no TLS) with the login of a claimed
physical node of the owner's: `KYIV_MQTT_USER` (the node's id in hex, no `!`) and `KYIV_MQTT_PASSWORD`
in `.env`, root topic `node/<that id>`. In `config/chatko.yaml` replace the `mqtt:` line of the
Meshtastic instance by the one in `config.example.yaml`, then `check-config`; the hub applies the
change itself. The physical node needs MQTT on with the same login, the mesh's primary channel and
the group's private channel with uplink and downlink on, "OK to MQTT" on and "Ignore MQTT" off.
Watch for `connected` in `docker compose logs meshtasticd-kyiv`; if the broker refuses the login the node
logs a connection failure every few seconds.

## Settings for a gateway of our own (D30, not used in v1)

`EU_433` region; "Ignore MQTT" off, "OK to MQTT" on; channel 0 `LongFast` with the Kyiv primary PSK
and channel 1 `family` with `MESH_FAMILY_PSK`, both with uplink and downlink on; MQTT enabled with
address `<host>:8883`, TLS on, the gateway's user and password from `.env`, root topic
`msh/EU_433`, encryption on, JSON off; Wi-Fi to the internet. Watch the broker with:

```bash
docker compose -f deploy/docker-compose.yml logs -f mosquitto
```
