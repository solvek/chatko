# Deploying chatko on a server

How to run the whole hub on a Linux server, keep it running, back it up and restore it. The design
behind it is [design.md §11](design.md#11-development-and-deployment) and decisions D30 and D57. Any
Linux server with Docker works, x86-64 or ARM64 (the images are built for both); the owner's runs on a
VPS, which is also the development machine (§8).

What runs ([`deploy/docker-compose.yml`](../deploy/docker-compose.yml)):

| Service | What it is | Reachable from |
|---|---|---|
| `chatko` | the hub (`chatko run`), image built from this repository | nothing connects to it |
| `mosquitto` | our MQTT broker, TLS on 8883, a user and an ACL per hub node and gateway; unused by radios in v1 (D58) | `127.0.0.1:8883` (the internet only with `MQTT_TLS_BIND=0.0.0.0`) |
| `meshtasticd-kyiv` | the hub's virtual Meshtastic node | `127.0.0.1:4413` (no authentication: never publish it) |
| `briar` | briar-headless from our fork (the private-group API) | `127.0.0.1:7000` (token) |

Outbound: Telegram (long polling), Tor and the Kyiv broker. Inbound: SSH only (8883 stays closed unless
you run gateways of our own). A server with 1 GB of RAM
is tight; the whole stack uses about 400 MB when idle (the hub 170 MB, briar-headless 230 MB), so 2 GB is
comfortable.

> **Tried:** the whole stack on the owner's VPS (S29, D57), a client over TLS on port 8883 (when it was
> public), a restore of a real backup onto an empty tree, and the `meshtasticd` and `chatko` images for
> ARM64 under QEMU. **Not tried:** the Kyiv broker (no login yet) and any radio (S23).

## 1. The server

1. **A Linux server** with a public address that does not change (a name is better still), 2 GB of RAM
   and 10 GB of disk. The stack needs no more than that, and nothing else must listen on 8883.
2. **Network.** Allow inbound **TCP 22** (from your address only, if you can); nothing else is needed
   (**TCP 8883**, from anywhere, only for gateways of our own). Docker
   publishes a port through its own chains, which `ufw` and the `INPUT` rules do not block, so the
   host is usually open already; if a gateway cannot connect while the container is up, look at both
   firewalls (`sudo iptables -L INPUT -n`).
3. **Docker.**
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-v2 docker-buildx git
   sudo systemctl enable --now docker       # the stack starts again after a reboot
   ```
4. **A user for the hub** that is not root, whose ids `setup.sh` puts in `.env` (§2):
   ```bash
   sudo useradd --system --home-dir /opt/chatko --shell /usr/sbin/nologin chatko
   ```

## 2. Install

```bash
sudo git clone <this repository> /opt/chatko
cd /opt/chatko
sudo SUDO_UID=$(id -u chatko) SUDO_GID=$(id -g chatko) deploy/setup.sh <the server's public IP or DNS name> gateway1
```

`setup.sh` makes the certificate (the name or IP is in it), the broker's users, passwords and ACL, and
every secret in `.env` (see [`deploy/README.md`](../deploy/README.md)). Run it again with all the
gateways' names to add one (§5). Then add what only you have to `.env`:

```bash
echo 'TELEGRAM_BOT_TOKEN=<from BotFather>' | sudo tee -a .env >/dev/null
```

**The briar-headless image.** It is built from our fork, which has no public home yet (D53): where its
checkout is (`../briar`, branch `1664-private-group-api`), build the image for the server's
architecture and, if that is another machine, copy it over:

```bash
docker buildx build --build-context briar=../briar --load -t chatko/briar-headless:1.5.21 deploy/briar
docker save chatko/briar-headless:1.5.21 | ssh <server> docker load      # not needed on the same machine
```

For ARM64 add `--platform linux/arm64` (an x86-64 build machine needs the handler once per boot:
`docker run --privileged --rm tonistiigi/binfmt --install arm64`). Once the fork is public,
`BRIAR_SRC=<its Git URL>#1664-private-group-api` in `.env` lets the server build it itself.

**The config.** Write `config/chatko.yaml` and `config/routing.py` from
[`config.example.yaml`](../config.example.yaml) and [`routing.example.py`](../routing.example.py). Its
addresses are the compose services' names already (`meshtasticd-kyiv:4403`, `mosquitto`,
`http://briar:7000`); `host: mosquitto, port: 1883, tls: false` is right for the hub's own node,
because that traffic stays inside the Docker network. `${NAME}` variables come from the `environment`
of the `chatko` service in `deploy/docker-compose.yml`: add a line there for each new one. Check it with
the image that will run it:

```bash
docker compose -f deploy/docker-compose.yml build chatko
docker compose -f deploy/docker-compose.yml run --rm --no-deps chatko check-config --config /app/config/chatko.yaml
```

**Start.**

```bash
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f chatko
```

The first start does a few things by itself: `briar` makes the account (nickname `chatko`) and
connects to Tor within a minute; `meshtasticd-kyiv` is provisioned by the hub, which reboots it once to
save the settings (a "cannot reach the node" warning right after is expected); the hub says
`the hub runs: …`. Every container restarts after a crash and after a reboot of the server
(`restart: unless-stopped`), and the logs rotate (5 files of 10 MB per container).

## 3. Join the hub to a Briar group

`briarctl` talks to briar-headless on `127.0.0.1:7000` (`BRIAR_API_PORT` in `.env` moves it). On the
server itself, from a checkout with `uv sync`, or from your own computer through an SSH tunnel, so
that nothing needs installing on the server:

```bash
ssh -N -L 7000:127.0.0.1:7000 <server> &
export BRIAR_AUTH_TOKEN=<the value in the server's .env>
uv run briarctl link
```

(Set `BRIARCTL_URL` if the port is not 7000.)

The steps after that (contacts, accepting the invitation, revealing contacts, the group id for
`chatko.yaml`) are in [`deploy/README.md`](../deploy/README.md#joining-the-hub-to-a-briar-group-briarctl).
The config reloads by itself when `config/chatko.yaml` changes.

## 4. Connect the hub's node to the Kyiv broker

The mesh side (D58): the owner's physical nodes talk LoRa to the rest of the Kyiv mesh, and at least
one of them has MQTT on and is claimed with the community, which gives it a login (its node id in
hex) and the root topic `node/<id>` on `mqtt.meshtastic.kyiv.ua`. The hub's virtual node uses the
**same login and root topic**, so it hears that node and is heard by it.

1. Put the login in `.env`: `KYIV_MQTT_USER=<node id in hex>`, `KYIV_MQTT_PASSWORD=<password>`, and
   `docker compose -f deploy/docker-compose.yml up -d` (the container reads `.env` when it is created).
2. In `config/chatko.yaml` swap the Meshtastic instance's `mqtt:` line for the one that refers to
   those variables (it is in the file as a comment, and in `config.example.yaml`).
3. `check-config` (§2); the hub applies the file by itself. The hub's node reboots once to save the
   new MQTT settings, and `docker compose … logs meshtasticd-kyiv` shows the connection.
4. Send a direct message to the hub's node from a radio and see it in the hub's log, and the other
   way round (a Telegram message reaches the radio with an ACK). The answers to design.md §6.6 go into spikes.md.

Until the login exists the hub's node uses our own Mosquitto, which no radio can reach, and its port
8883 is closed to the internet (`MQTT_TLS_BIND`, `127.0.0.1` by default). To use gateways of our own
instead, open it with `MQTT_TLS_BIND=0.0.0.0` in `.env`, run `deploy/setup.sh <host> gateway1 …`, and set
the gateway up as in [`deploy/README.md`](../deploy/README.md#settings-for-a-gateway-of-our-own-d30-not-used-in-v1);
check it with `mosquitto_pub -h <server> -p 8883 --cafile server.crt -u gateway1 -P '<its password>' -t msh/EU_433/test -m hello`
(the certificate is `config/mosquitto/tls/server.crt`): it must connect, and refuse anonymous clients
and wrong passwords.

### The radio nodes

A member's (or the owner's) physical node needs the same channel 0 as the hub's node, or the ACKs of direct messages
are lost and the hub retries a delivery again and again (spikes.md S23, D63). `deploy/configure-node.sh` sets a node over USB
in one go: region `EU_433`, slot 1, Ignore MQTT off, OK to MQTT on, channel 0 the private channel, channels 1 and 2 the Kyiv
`LongFast` and `KyivUA`, and, for a node with internet, its MQTT client (encryption on, proxy to client on):

```bash
CHATKO_PSK=<the key in .env as WIKIMESH_PRIVATE_PSK> deploy/configure-node.sh --port /dev/ttyACM0
```

The key goes on the command line: run it on your own machine and never commit it. A node with MQTT encryption on drops the
community's decoded envelopes, so it does not list the community's nodes; that is the price of the private traffic with the hub.

## 5. Operations

Every command below runs in `/opt/chatko`; `dc` stands for `docker compose -f deploy/docker-compose.yml`.

| Task | How |
|---|---|
| Is it up? | `dc ps` (briar shows `healthy`); the hub's log says `the hub runs` |
| Read the logs | `dc logs -f chatko` (also `briar`, `mosquitto`, `meshtasticd-kyiv`); `--since 1h` |
| Change the config or routing | edit `config/chatko.yaml` or `routing.py`; the hub reloads them and keeps the old config if the new one is wrong (the log says why). `check-config` (§2) tells first |
| Add a gateway of our own (D30) | `deploy/setup.sh <host> gateway1 gateway2`, then `dc restart mosquitto` |
| Update chatko | `git pull && dc up -d --build chatko`; stops take a couple of seconds |
| Update `meshtasticd` | change `MESHTASTICD_TAG` in `.env`, run the lab tests first (`uv run pytest -m lab`), then `dc up -d` |
| Restart everything | `dc restart`; `dc down` and `dc up -d` keep the data |
| The server's address changed | delete `config/mosquitto/tls`, run `deploy/setup.sh <new host> <gateway names…>`, `dc restart mosquitto`, and point the gateways at the new address |
| Change a secret | edit `.env`, then `dc up -d` (containers read it at creation). A new gateway password: delete its line, run `setup.sh` again, restart `mosquitto` |

What is in the data, and what its loss costs:

| Path | Holds | If lost without a backup |
|---|---|---|
| `.env` | all secrets, among them `BRIAR_PASSWORD` and `MESH_KYIV_PRIVATE_KEY` | the Briar account is unreadable (new account, new contacts and groups); the node gets a new identity, so members' radios must forget the old one |
| `config/` | `chatko.yaml`, `routing.py`, the broker's certificate, users and ACL | rewrite them |
| `data/briar` | the hub's Briar account, contacts, groups and messages | as above |
| `data/chatko` | messages awaiting delivery, the retention window, accounts seen | pending deliveries and the recent history are gone |
| `data/meshtasticd-kyiv` | the node's settings and learned keys | the hub provisions it again and relearns keys; keys in the config's `contacts` come back at once |

## 6. Backups

[`deploy/backup.sh`](../deploy/backup.sh) puts `.env`, `config/` and `data/` into one archive
(`backups/chatko-<UTC time>.tar.gz`, about 25 MB), keeps the newest 14 (`BACKUP_KEEP`), and takes the
hub and `briar` down for a few seconds while it copies, because SQLite and Briar's database must not
be copied while they run. Both catch up on what they missed when they start (the outbox and the Briar
read flag, architecture.md §5.2, design.md §7.2). It runs as root, since the containers' users own part of `data/`.

Daily, with a systemd timer (03:30 server time; a missed run happens at the next boot):

```bash
sudo cp deploy/systemd/chatko-backup.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chatko-backup.timer
systemctl list-timers chatko-backup.timer
sudo systemctl start chatko-backup.service && journalctl -u chatko-backup.service -n 20
```

(The service file assumes `/opt/chatko`.)

**Copy the archives off the server**, or a lost VM takes the backups with it. They hold every secret,
so copy them encrypted: for example with `rclone` and a `crypt` remote, or `restic`. Uncomment and
adapt the `ExecStartPost` line in `/etc/systemd/system/chatko-backup.service`. Without it the
backups protect only against a mistake, not against losing the server.

**Restore**, onto this server or a new one (install Docker and clone the repository first, §1–2; no
`setup.sh` is needed, since the archive has the secrets and the certificate):

```bash
sudo deploy/restore.sh /path/to/chatko-<time>.tar.gz
docker compose -f deploy/docker-compose.yml up -d
```

It stops the stack, moves what it finds to `replaced-<time>/` (it deletes nothing) and unpacks the
archive with the original owners. On a new server also copy the briar-headless image (§2) and, if the
address changed, make a new certificate (§5). The Briar account and its contacts come back as they
were, and so does the Meshtastic node's identity, because both are in the backup.

**Test the restore** after you set the timer up, and now and then: restore the newest archive into
a scratch copy of the repository (on another machine, or here with a different `COMPOSE_PROJECT_NAME`
and the three host ports changed in `.env`: `MQTT_TLS_PORT`, `MESH_API_PORT`, `BRIAR_API_PORT`; take
`TELEGRAM_BOT_TOKEN` and the Telegram parts out of its config, since two hubs must not poll one bot),
start it, and check that `briarctl link` shows the
same link as the production hub and that the hub's log has no errors. Do not run two copies against
the same Telegram bot token or the same Briar account at once.

## 7. Problems

- **The `briar` container restarts in a loop.** The password in `.env` is not the account's. The log
  says so; restore the right `.env`, or delete `data/briar` and start over with a new account.
- **`chatko` keeps logging `lost briar-headless` or `cannot reach the node`.** The other container is
  starting (a minute at most), or is down: `dc ps`.
- **A gateway does not connect.** Compare its settings with `deploy/README.md`; read the broker's log;
  check the security list, then the host firewall (§1).
- **Messages for a Briar group wait.** The hub is not a member of the group in `chatko.yaml`: the
  admin notice says which (`briarctl invitation list`).
- **The disk fills.** `docker system df`; `docker system prune` removes only unused images and build
  cache, never the data in `data/`.

## 8. This installation (the owner's VPS)

One machine is both the server and the development machine, so the two are kept apart:

| | Production | Development |
|---|---|---|
| Directory | `/opt/chatko` (a snapshot of the repository without `.git`) | `~/Projects/chatko` |
| Compose project | `chatko` | `chatko-dev` (`COMPOSE_PROJECT_NAME` in its `.env`) |
| Host ports | all on `127.0.0.1`: 8883 (Mosquitto), 4413, 7001 for briar-headless | 18883, 14413, 17000 |
| Secrets | its own `.env`, with the production bot's token | its own `.env`; no bot token until a development bot exists |
| Briar account | the real one (D60): the people's contacts and the group «Кризовий чатко» | a new one in `lab/briar` with no contacts |
| Groups | `crisis`: Telegram supergroup «Кризовий Чатко» ⇄ the Briar group (default routing) | `dev` (Meshtastic only, until a development bot exists) |
| Node | `chatko` / `CHKO` (`!c4a7c001`); on the community broker `mqtt.wikimesh.in.ua`, root `kyiv`, encryption on, a private channel `chatkotest` and `dm` to the owner's node (D63) | `chatko dev` / `DEV` |
| Runs as | user `chatko` (the hub), systemd timer `chatko-backup.timer` | the local lab (`lab/`) and `uv run` |

The lab (`lab/`, ports 1883, 4403–4405 and briar-headless on 7000) is development too. Real state
lives in production and development is the one made new (D60): never move a Briar account back, and never
run one account in two places. To update production from the development checkout, copy the changed files over (there is no
`.git` in `/opt/chatko`) and rebuild:

```bash
rsync -a --exclude='.git/' --exclude='.venv/' --exclude='__pycache__/' --exclude='.*_cache/' \
    --exclude='/config/' --exclude='/data/' --exclude='/.env' --exclude='/backups/' \
    --exclude='/replaced-*/' --exclude='/lab/briar/.env' ~/Projects/chatko/ /opt/chatko/
cd /opt/chatko && BRIAR_SRC=~/Projects/briar docker compose -f deploy/docker-compose.yml up -d --build chatko
```

`BRIAR_SRC` is only there so that compose finds the build context of the `briar` service (it reads every one); the
briar image is not rebuilt.
