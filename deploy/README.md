# Server stack

Our Mosquitto (TLS on 8883 for our gateways), the hub's virtual Meshtastic node and its
briar-headless, as in design.md §11, D30 and D53. chatko itself runs next to them (`uv run chatko
run`) and reaches the node on `127.0.0.1:4413` and briar-headless on `127.0.0.1:7000`. S29 will turn this into the full production setup (chatko in a container, backups).

```bash
deploy/setup.sh <public host name or IP> [gateway names...]
```

```bash
docker compose -f deploy/docker-compose.yml up -d
```

`setup.sh` is idempotent. It makes the self-signed certificate (`config/mosquitto/tls/`), one
Mosquitto user per hub node and per gateway with an ACL limited to the root topic `msh/EU_433/#`,
and the secrets in `.env` (`MQTT_PASSWORD_<USER>`, `MESH_FAMILY_PSK`; `KYIV_PRIMARY_PSK` is the
Kyiv mesh's public key from spikes.md S2; `BRIAR_PASSWORD`, `BRIAR_AUTH_TOKEN`), and links `.env` as
`deploy/.env`, where Compose reads it. Add a gateway by running it again with all the names. Only
8883 is published; the node's API (4413) and briar-headless's (7000) are on `127.0.0.1`.

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


## Settings for our physical gateway (S23)

`EU_433` region; "Ignore MQTT" off, "OK to MQTT" on; channel 0 `LongFast` with the Kyiv primary PSK
and channel 1 `family` with `MESH_FAMILY_PSK`, both with uplink and downlink on; MQTT enabled with
address `<host>:8883`, TLS on, the gateway's user and password from `.env`, root topic
`msh/EU_433`, encryption on, JSON off; Wi-Fi to the internet. Watch the broker with:

```bash
docker compose -f deploy/docker-compose.yml logs -f mosquitto
```
