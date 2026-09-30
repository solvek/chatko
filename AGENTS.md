# chatko: guide for AI agents and contributors

## Rules
- Everything in the repository is in **English**: code, comments, docs, commit messages. The project
  owner talks in Ukrainian in chat; answer in Ukrainian there.
- This is a **public open-source repository** (GPL-3.0-or-later). Never commit secrets: they go to
  `.env` (git-ignored) and are referenced from YAML as `${VAR}`. `config/` and `data/` are git-ignored
  as well.
- **Code quality is a hard requirement**: clean architecture, `mypy --strict`, `ruff`, tests for every
  behaviour, coverage gates. Read [docs/architecture.md](docs/architecture.md) before writing code, and
  keep to its dependency rule: the core never imports an extension, and extensions import only
  `chatko.extension_api`.
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
- `config.example.yaml`, `.env.example`: configuration format.

## Current state
- Design phase; no code yet. Phase 0 (spikes S1–S3) comes next, then phase 1 (core). The next session
  is the first one marked `todo` in `docs/roadmap.md`.
- Development runs locally on the owner's Linux machine. There is no hardware Meshtastic node yet, so
  a second virtual node (`meshtasticd`) plays the member's radio. The production host will be some Linux
  server (Oracle Cloud Always Free is a candidate).
- The owner has 3 Android phones for the Briar test.

## Background
- The project started as a design discussion on 2026-09-30. The owner also has a related Kotlin
  project, `~/Projects/Chatway` (a Telegram ⇄ Meshtastic router for Android). Its
  `docs/meshtastic-notes.md` (MQTT topics, packet format, crypto) and `docs/similar-projects.md` are
  useful references.
- mr-tbot/mesh-api was evaluated and rejected as a base (decision D2).
- The default MQTT broker is the Kyiv community broker, but each Meshtastic extension instance can use
  any broker. The owner will have hardware gateway nodes later.
