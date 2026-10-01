# chatko

**chatko keeps a closed group of people talking by text when some of them have no mobile network or
internet.** A group lives in several networks at once: a Telegram group, a Briar private group, and
Meshtastic LoRa channels. A hub copies every message to all the other places.

```
 Telegram group ◄──► chatko hub ◄──► Meshtastic channel (virtual node → MQTT → gateway node → radios)
                         ▲
                         └──► Briar private group (members also relay it to each other over Bluetooth/Wi-Fi)
```

- One installation serves several independent groups.
- Every network is an **extension**: Telegram, Meshtastic and Briar today, and more can be added later.
- Where each message goes, and how its author is signed, is a small Python script the admin writes.
  Without it, every message goes to all the other places of its group.
- It can forward the mesh's primary Meshtastic chat (e.g. the Kyiv `LongFast`) to selected chats.

> **Status: phase 1 (core) is done; phase 2 (Telegram) is in progress.** The project skeleton, tooling, domain model, the extension and routing APIs, the inbound pipeline with the outbox worker, the routing engine with a test kit for routing scripts, SQLite storage, the configuration and `chatko run` are in place and reviewed. The Telegram extension is written and tested, and waits for its first live test with a real bot; Meshtastic and Briar come next. Start with the [design](docs/design.md).

## Known limitations

- **A cloud hub reaches radios only through an internet-connected gateway node.** A radio node without
  internet only relays. chatko runs its own MQTT broker, and at least one gateway of your own (a radio
  with internet near the group) connects to it; community brokers give access only to registered
  physical nodes. chatko sends to radios as direct messages to each member's node (one packet per
  node, with delivery confirmation), or as one broadcast on a private group channel. A hub within
  radio range will be able to use a physical node instead and need no gateway (not in v1).
- **Radios must not ignore MQTT, and must allow it.** Messages from a cloud hub reach the air through
  MQTT, and a radio with "Ignore MQTT" on drops them (and does not relay them). Meshtastic turns this
  setting on when a region with a duty-cycle limit (`EU_433`, `EU_868`, `UA_433`, `UA_868`) is first
  set, so members turn it off on their radios.
  Members also turn "OK to MQTT" on (it is off by default): without it, gateways on a public broker do
  not pass their messages and acknowledgements on to the hub.
- **Direct messages to radios need keys on both sides.** Meshtastic encrypts a direct message with the
  receiver's key, and a node keeps the first key it learns for another node. When a member resets
  their radio, the admin puts its new key into the config. Keep the hub's own key in the config
  (`private_key`), so that it never changes, even if the hub's data is lost.
- **Removing a person is done in each network, and is not instant everywhere.** chatko manages no
  members: who is in a Telegram group is up to its admins, and the Meshtastic nodes and channels are
  in the config. To remove someone from a Meshtastic channel, set a new channel key on all radios.
  Briar cannot remove a member from a private group, and deleting the contact does not cut them off
  (they still sync through other members). The only remedy is to re-create the Briar group.
- **Briar reaches offline members only through other members.** A member who is never online gets Briar
  messages only if another group member (a Briar contact of theirs) syncs with the hub and later meets
  them, and one of the two has used "Reveal contacts" in the group. Direct Briar messages are never
  relayed like this, which is one reason chatko has group messages only.
- **The hub is a single point of failure.** Keep backups of `config/` and `data/`.

## Development

Python 3.12+ and [uv](https://docs.astral.sh/uv/). Every gate that CI runs:

```bash
uv sync
uv run pre-commit install        # once: runs ruff, mypy and lint-imports before each commit
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run coverage run -m pytest && uv run coverage report
```

Check a config and its routing script (and run the script's tests, which needs the `test` extra,
`uv sync --extra test`; the dev group has `pytest` already):

```bash
uv run chatko check-config --config config/chatko.yaml    # reads .env too
```

Run the hub (until Ctrl+C or SIGTERM; its state goes to `data/chatko.sqlite3`, and it reloads
`chatko.yaml` and the routing script when they change):

```bash
uv run chatko run --config config/chatko.yaml --data data    # reads .env too
```

The gates and their limits are in [docs/architecture.md §7](docs/architecture.md#7-quality-gates).

## Documentation

- [Design](docs/design.md): behaviour, concepts, extensions, routing, configuration, plan
- [Architecture](docs/architecture.md): code structure, extension API, quality gates
- [Decisions](docs/decisions.md): key decisions and why
- [Spikes](docs/spikes.md): experiments that answer the open questions
- [Roadmap](docs/roadmap.md): the plan by working session
- [`config.example.yaml`](config.example.yaml), [`.env.example`](.env.example): configuration format
- [AGENTS.md](AGENTS.md): guide for contributors and AI coding agents

## Built on

- [meshtasticd](https://meshtastic.org/docs/software/linux/): the official Meshtastic Linux node, used as the virtual node
- [briar-headless](https://code.briarproject.org/briar/briar/-/tree/master/briar-headless): the Briar REST API peer
- Telegram Bot API

## License

[GPL-3.0-or-later](LICENSE).
