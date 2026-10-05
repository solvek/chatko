# chatko: guide for AI agents and contributors

## Rules
- Everything in the repository is in **English**: code, comments, docs, commit messages. The project
  owner talks in Ukrainian in chat; answer in Ukrainian there.
- This is a **public open-source repository** (GPL-3.0-or-later). Never commit secrets: they go to
  `.env` (git-ignored) and are referenced from YAML as `${VAR}`. `config/` and `data/` are git-ignored
  as well.
- **Code quality is a hard requirement**: clean architecture, `mypy --strict`, `ruff`, tests for every
  behaviour, coverage gates. Read [docs/architecture.md](docs/architecture.md) before writing code, and
  keep to its dependency rule: the core never imports an extension, extensions import only
  `chatko.extension_api`, and routing scripts import only `chatko.routing_api`.
- **No network is special.** Telegram, Meshtastic and Briar are extensions. Don't put
  network-specific logic in the core.
- Record significant decisions in `docs/decisions.md` (append-only). Keep `docs/design.md` (behaviour)
  and `docs/architecture.md` (code structure) up to date in the same change.
- Commit or push only when the owner asks.

## Where things are
- `docs/design.md`: behaviour, the source of truth for what the system does, and the phase plan.
- `docs/architecture.md`: layers, packages, extension API, technology, quality gates.
- `docs/decisions.md`: why things are the way they are.
- `docs/spikes.md`: phase-0 experiments and their results. Record results there.
- `docs/roadmap.md`: the plan by working session, with the model and effort for each. Mark sessions
  done there.
- `config.example.yaml`, `routing.example.py`, `.env.example`: configuration format.
- `lab/`: the local Meshtastic lab (`docker compose`: Mosquitto and two `meshtasticd` nodes) and the
  spike scripts; see `lab/README.md`.

## Current state
- The project skeleton (S08: `pyproject.toml`, packages under `src/`, CI, `pre-commit`; run the
  gates as in the README), the domain model (S09: `chatko.domain`), the two public APIs with
  their test kits (S10: `chatko.extension_api`, `chatko.routing_api`, D35, D36) and the application
  layer's pipeline, invariants, outbox worker and `HubContext` over in-memory fakes (S11, D37) and
  the routing engine with the admin's test kit for routing scripts (S12, D39), the SQLite
  repositories (S13, D40), the configuration with `chatko check-config`, the admin notifier and
  extension discovery (S14, D41) and the running hub, `HubRuntime` with `chatko run` (S15, D42),
  exist, and the phase 1 review (S16, D43) fixed what it found. The Telegram extension
  (`chatko_telegram`, S17, D44) is tested against a fake of its `TelegramApi` port and
  passed the live test with a real bot and groups (S18, D45). The Meshtastic extension has its
  base (`chatko_meshtastic`, S19, D46): the config model, the `MeshApi` port with its adapter over
  the `meshtastic` library and its fake, provisioning and `MeshNode`, tested against the lab
  (`uv run pytest -m lab`); `MeshtasticExtension` is registered with its `channel` endpoints
  (S20, D47) and its `dm` endpoints: direct messages per node by the node's ACK, retries woken
  by what the hub hears, favorites, key-mismatch notices (S21, D49). The whole hub relays between a fake Telegram
  and the lab's radio with both kinds of endpoint (`tests/integration/test_meshtastic_lab_relay.py`,
  run nightly in CI by `.github/workflows/lab.yml`; S22, D50). Our briar-headless fork has the
  private-group API (S24, D51), tried with a phone; `deploy/briar/` builds its image for amd64 and
  arm64 for the server and the lab, and the upstream merge request waits for the owner (S25, D53,
  `docs/briar-merge-request.md`). The Briar extension (`chatko_briar`, S26, D54) is tested against a fake of its `BriarApi` port. `briarctl` (S27, D55, `src/tools/briarctl`) is built and tried on the lab with a phone. One group works across Telegram, Briar and Meshtastic (a channel and DMs to two lab nodes) with the whole hub on the lab (S28, D56, `lab/three-networks/`). The
  production stack (S29, D57: `deploy/`, `docs/deployment.md`) has the hub in a container and a daily
  backup (`backup.sh`, `restore.sh`); it runs in production on the owner's VPS (S29 done) and is tried with a TLS client, a restore
  and the arm64 images under QEMU; production mirrors the Telegram supergroup «Кризовий Чатко» and the Briar group of the same name, tested both ways (D60); no radio has connected yet: v1 uses the Kyiv broker with the login of a physical node of the owner (D58). The owner's node is flashed (2.8.1, new keys, region `EU_433`, D61) and the first credentials came for another broker, so nothing is connected yet (D62, S23). Every
  extension passes the contract suite in `chatko.extension_api.testing`. Phase 0 (spikes S1–S3) is closed (D30): the
  Briar relay on phones, the local Meshtastic lab in `lab/` (channel and direct messages, keys,
  ACKs, provisioning), briar-headless in Docker in `lab/briar/` and the plan for its private-group
  patch (D29). The Kyiv broker questions (design.md §6.6) are open and now decide how well the Meshtastic path works (D58).
  Phases 1 (core), 2 (Telegram) and 4 (Briar) are closed; phase 3 (Meshtastic) waits for its
  field test S23, which needs hardware: the next session is the first one marked `todo` in
  `docs/roadmap.md` that can run. S29 is done, so S30 is next if S23's hardware is still missing.
- The upstream Briar clone for reading and patching is `~/Projects/briar` (tag `release-1.5.21`); its
  branch `1664-private-group-api` on that tag is our fork with the private-group patch (S24, D51).
  Build and test it with JDK 17 in Docker (`eclipse-temurin:17-jdk`,
  `./gradlew --configure-on-demand briar-headless:test`).
- Development runs on the owner's Linux machine, which is also the production server (a VPS, no
  Oracle or other VM). **Production runs from `/opt/chatko`** (compose project `chatko`, the
  production Telegram bot, a snapshot without `.git`; updating and the table of what differs are in
  `docs/deployment.md` §8). **This checkout is development:** its `.env` and `config/` have their own
  keys and names (`COMPOSE_PROJECT_NAME=chatko-dev`, other host ports, node `chatko dev`, its own new Briar account) and no bot
  token until the owner makes a development bot. Never put production's token or groups here, and
  never touch `/opt/chatko` except to deploy. There is no hardware Meshtastic node yet, so a second
  virtual node (`meshtasticd`) plays the member's radio.
- The owner has 3 Android phones for the Briar test.

## Background
- The project started as a design discussion on 2026-09-30. The owner also has a related Kotlin
  project, `~/Projects/Chatway` (a Telegram ⇄ Meshtastic router for Android). Its
  `docs/meshtastic-notes.md` (MQTT topics, packet format, crypto) and `docs/similar-projects.md` are
  useful references.
- mr-tbot/mesh-api was evaluated and rejected as a base (D2), and re-evaluated with the same result (D19).
- v1 scope (D20): one hub in the cloud that syncs a Briar group, a Telegram group and Meshtastic
  (direct messages to several nodes; `channel` endpoints are supported but not used, D59) through a virtual node. Routing is an admin-written Python script
  (D16), and so is author labelling (D21). The hub only relays: no member management, commands or
  control surface (D22); channels, PSKs, `dm` node lists and optional people are in the config.
  People create the Briar groups and invite the hub, which reveals its contacts there; a group the
  hub creates is the fallback (D52). The admin manages the hub's contacts, groups and invitations
  with `briarctl`, a separate command-line tool outside chatko's architecture (D24). A
  physical node and several hubs (e.g. a home Raspberry Pi) are later (D17).
- v1 reaches the Kyiv mesh through the Kyiv community broker (D58, superseding D30's own gateway): the
  owner's several physical nodes sit in the mesh, one with MQTT on is claimed (logins go only to claimed
  physical nodes, D27), and the hub's virtual node uses its login and root topic `node/<id>`. The login
  does not exist yet, so production's node uses our own Mosquitto meanwhile. Each Meshtastic extension
  instance can use any broker.
