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
- Design phase; no code yet. Phase 0 (spikes S1–S3) is in progress (spike S1, Briar relay, and the lab
  parts of spike S2, the local Meshtastic lab in `lab/` with channel and direct messages, keys, ACKs
  and provisioning, and part 1 of spike S3, briar-headless built and run in Docker in `lab/briar/`
  with contacts and private messages to a phone, are done), then phase 1 (core). The next session is the first one marked `todo` in
  `docs/roadmap.md`.
- Development runs locally on the owner's Linux machine. There is no hardware Meshtastic node yet, so
  a second virtual node (`meshtasticd`) plays the member's radio. The production host will be some Linux
  server (Oracle Cloud Always Free is a candidate).
- The owner has 3 Android phones for the Briar test.

## Background
- The project started as a design discussion on 2026-09-30. The owner also has a related Kotlin
  project, `~/Projects/Chatway` (a Telegram ⇄ Meshtastic router for Android). Its
  `docs/meshtastic-notes.md` (MQTT topics, packet format, crypto) and `docs/similar-projects.md` are
  useful references.
- mr-tbot/mesh-api was evaluated and rejected as a base (D2), and re-evaluated with the same result (D19).
- v1 scope (D20): one hub in the cloud that syncs a Briar group, a Telegram group and Meshtastic
  (a channel or DMs to several nodes) through a virtual node. Routing is an admin-written Python script
  (D16), and so is author labelling (D21). The hub only relays: no member management, commands or
  control surface (D22); channels, PSKs, `dm` node lists and optional people are in the config. The
  hub's Briar account creates the Briar groups; the admin manages contacts, groups and invitations with
  `briarctl`, a separate command-line tool outside chatko's architecture (D24). A
  physical node and several hubs (e.g. a home Raspberry Pi) are later (D17).
- The default MQTT broker is the Kyiv community broker, but each Meshtastic extension instance can use
  any broker. The owner will have hardware gateway nodes later.
