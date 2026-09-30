# Architecture

How the code is organized. What the system does is in [design.md](design.md).

## 1. Principles

1. **Clean architecture with a strict dependency rule.** Dependencies point inwards only:

   ```
   domain  ◄──  application  ◄──  extension_api  ◄──  extensions (telegram, meshtastic, briar, …)
      ▲              ▲                                        │
      └──────────────┴────────  infrastructure, app  ◄────────┘ (the composition root wires everything)
   ```

   - `domain` knows nothing about I/O, asyncio, databases or networks.
   - `application` holds the use cases and defines **ports** (interfaces) for what it needs from outside.
   - Extensions depend only on the public `chatko.extension_api` package and never on core internals.
     The core never imports an extension.
   - The rule is enforced in CI with `import-linter` contracts.
2. **Every network is an extension**, including Telegram. The core names no network, and no extension
   is treated specially. Removing an extension from the config (or uninstalling it) must leave a
   working core.
3. **Capabilities, not inheritance chains.** An extension declares which capabilities it provides
   (legs, membership source, control surface, commands, feeds). Telegram provides all of them;
   Meshtastic provides legs, commands and feeds; Briar provides legs and commands.
4. **Testable by construction.** All I/O is behind ports, so every use case is tested with fakes and
   without a network. Time, ids and randomness are injected.

## 2. Package layout

```
src/
  chatko/
    domain/          # entities and pure rules: Member, Identity, Nick (+ generator), Group, LegRef,
                     # Message, Delivery; no I/O
    application/     # use cases: route inbound message, deliver outbox, handle command, sync membership,
                     # link/unlink identity; ports: repositories, clock, id generator
    extension_api/   # the ONLY package extensions may import: Extension base, capability protocols,
                     # HubContext, message/command/reply types
      testing/       # contract test suite + fakes that every extension runs against itself
    infrastructure/  # SQLite repositories, config loading (YAML + ${ENV}), extension discovery,
                     # logging
    app/             # composition root, CLI: `chatko run`, `chatko check-config`
  chatko_telegram/   # extension packages: separate top-level packages, so importing core internals
  chatko_meshtastic/ # is visible and forbidden by import-linter
  chatko_briar/
tests/
  unit/              # domain and application, with fakes
  contract/          # each extension against extension_api.testing
  integration/       # real SQLite; opt-in docker lab (Mosquitto + meshtasticd, briar-headless)
```

Built-in extensions are shipped in the same repository and distribution for now, but they are registered
exactly like third-party ones: through the `chatko.extensions` entry point group in `pyproject.toml`.
Any of them can move to its own package later without code changes.

## 3. Extension API

A sketch; the real signatures are settled in phase 1 together with the contract tests.

```python
class Extension(ABC):
    type_name: ClassVar[str]                  # "telegram", "meshtastic", …
    config_model: ClassVar[type[BaseModel]]   # validates the instance's config section

    def __init__(self, instance: str, config: BaseModel, hub: HubContext) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

An extension implements any of these capability protocols:

| Capability | Methods (extension side) | Calls into the hub |
|---|---|---|
| `LegProvider` | `leg_config_model`; `attach_leg(leg, cfg)`, `detach_leg(leg)`; `deliver(leg, OutboundMessage) → DeliveryResult` | `hub.submit(InboundMessage)` |
| `MembershipSource` | `source_config_model`; `current_members(source) → set[IdentityKey]`; `display_name(identity)` | `hub.membership_changed(source, joined, left)` |
| `ControlSurface` | `send(identity, Reply)` (text, buttons, image such as a QR code) | `hub.command(identity, CommandInvocation)` |
| `CommandProvider` | `commands() → list[CommandSpec]` with async handlers `(CommandContext) → Reply` | — |
| `FeedSource` | `feed_config_model` | `hub.feed_item(source, FeedItem)` |

`HubContext` is the only thing the core gives an extension. It can:
- submit inbound messages, membership changes, commands and feed items;
- resolve an identity to a member and nick;
- link and unlink identities for a member;
- reach a namespaced key–value store for the extension's own state (e.g. Briar contact ids, node keys
  seen);
- notify the admins;
- use a logger and a clock.

Key types:
- `IdentityKey(kind, external_id)`, e.g. `("telegram", "123")`, `("meshtastic", "!a1b2c3d4")`.
- `InboundMessage(leg, external_id, author: IdentityKey | ForeignAuthor, text, attachments)`, where
  `external_id` is used for de-duplication.
- `OutboundMessage(message_id, author_label, text, attachments)`. The core supplies `author_label`
  (`NatAda` or `~BC1`). Each extension renders the final text for its network, e.g. the Meshtastic
  extension splits it into 200-byte parts.
- `DeliveryResult`: `Delivered` | `Retry(after)` | `Failed(reason)`, plus an optional `truncated`
  flag.

Rules for extension authors:
- Never block the event loop. Wrap thread-based client libraries (e.g. `meshtastic`'s `TCPInterface`)
  in an adapter that bridges to asyncio with `loop.call_soon_threadsafe`.
- Talk to the external system through a small internal port (e.g. `TelegramApi`, `BriarApi`), so that
  the extension logic is tested against a fake of that port.
- Drop the hub's own posts before calling `hub.submit`.

## 4. Core services (application layer)

| Service | Responsibility |
|---|---|
| `InboundRouter` | dedup → resolve author → resolve group from leg → persist message → create one outbox row per other leg |
| `OutboxWorker` | take due rows, call `deliver`, apply `DeliveryResult` with exponential backoff; survives restarts |
| `CommandService` | parse and authorize commands, run core and extension handlers, return a `Reply` |
| `MembershipService` | create members on first sight, keep group membership in sync with sources, trigger follow-ups (e.g. Briar invitation when a member joins) through events |
| `IdentityService` | link/unlink identities, enforce uniqueness (one node → one member, at most one Briar identity) |
| `NickService` | generate, validate and change nicks |
| `FeedService` | forward feed items to the listed members' control surfaces |
| `ConfigService` | load, validate (core + each extension's models) and hot-reload the config; keep the last valid one |

Follow-ups between services go through a small in-process event bus (`MemberJoinedGroup`,
`IdentityLinked`, …). Extensions subscribe to it through `HubContext`.

## 5. Technology

| Area | Choice |
|---|---|
| Language | Python 3.12+, asyncio, full type hints |
| Tooling | `uv` (environments, lock file), `ruff` (lint + format), `mypy --strict`, `import-linter`, `pre-commit` |
| Tests | `pytest`, `pytest-asyncio`, `coverage` (branch); `respx`/local test servers for HTTP fakes |
| Config | `pydantic` v2 models, YAML (`PyYAML`, safe loader), `${ENV}` substitution, `watchfiles` for reload |
| Storage | SQLite via `aiosqlite`, schema migrations in code |
| Telegram | `aiogram` 3, wrapped behind the extension's `TelegramApi` port |
| Meshtastic | official `meshtastic` Python library over TCP to `meshtasticd` |
| Briar | `httpx` + `websockets` to `briar-headless` |
| License | GPL-3.0-or-later |

## 6. Quality gates

Every pull request must pass in CI (GitHub Actions; Linux, macOS and Windows for the hub):

- `ruff check`, `ruff format --check`;
- `mypy --strict`;
- `lint-imports` (the dependency rule);
- `pytest` with branch coverage: **≥ 95 %** for `domain` and `application`, **≥ 85 %** for each
  extension, **≥ 90 %** overall;
- the contract test suite for every built-in extension.

Integration tests with the docker lab run on demand and nightly; they are not required for every PR.

Testing style:
- Test behaviour through public APIs, not private helpers. One behaviour per test, with a descriptive
  name.
- Unit tests never touch the network, the real clock or the filesystem outside `tmp_path`.
- Every bug fix starts with a failing test.
- Fakes (in-memory repositories, `FakeExtension`, fake ports) live in the code base next to the ports
  they fake, not as ad-hoc mocks in tests.
