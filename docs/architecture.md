# Architecture

How the code is organized. What the system does is in [design.md](design.md).

## 1. Principles

1. **Clean architecture with a strict dependency rule.** Dependencies point inwards only:

   ```
                             ┌──  extension_api  ◄──  extensions (telegram, meshtastic, briar, …)
   domain  ◄──  application ◄┤
                             └──  routing_api    ◄──  the admin's routing script (config/routing.py)
      ▲              ▲
      └──────────────┴────────  infrastructure, app   (the composition root wires everything)
   ```

   - `domain` knows nothing about I/O, asyncio, databases or networks.
   - `application` holds the use cases and defines **ports** (interfaces) for what it needs from outside.
   - Extensions depend only on the public `chatko.extension_api` package and never on core internals.
     The core never imports an extension.
   - The routing script depends only on the public `chatko.routing_api` package. It is loaded at run
     time from the config directory; the core never imports it by name.
   - `briarctl` (a command-line tool for the hub's Briar account) is independent of all of the above:
     it only calls the `briar-headless` REST API (D24).
   - The rule is enforced in CI with `import-linter` contracts.
2. **Every network is an extension**, including Telegram. The core names no network, and no extension
   is treated specially. Removing an extension from the config (or uninstalling it) must leave a
   working core.
3. **The hub relays; it does not manage people.** Extensions provide endpoints only. There are no
   member records, commands or control surfaces in v1 (D22); people in the config are plain data for
   author labels.
4. **Testable by construction.** All I/O is behind ports, so every use case is tested with fakes and
   without a network. Time, ids and randomness are injected.
5. **Routing is data flow, not side effects.** The routing script is a pure function that returns
   targets; the core enforces the invariants and does all delivery through the outbox (§4).

## 2. Package layout

```
src/
  chatko/
    domain/          # entities and pure rules (§2.1): EndpointRef, Group, Account, Person, Message,
                     # Target, Delivery, Topology, fingerprint, label generator; no I/O
    application/     # use cases: inbound pipeline, routing engine, routing invariants, outbox worker,
                     # admin notices; ports: repositories, clock, id generator
    extension_api/   # the ONLY package extensions may import: Extension base, EndpointProvider,
                     # HubContext, message and delivery types
      testing/       # contract test suite + fakes that every extension runs against itself
    routing_api/     # the ONLY package a routing script may import: RoutedMessage, RoutingContext,
                     # targets, mirror(), helpers
      testing/       # fake installation and assertions for the admin's routing tests
    infrastructure/  # SQLite repositories, config loading (YAML + ${ENV}), extension discovery,
                     # logging
    app/             # composition root, CLI: `chatko run`, `chatko check-config`
  chatko_telegram/   # extension packages: separate top-level packages, so importing core internals
  chatko_meshtastic/ # is visible and forbidden by import-linter
  chatko_briar/
  briarctl/          # the admin's CLI for the hub's Briar account (design.md §7.5); a separate program:
                     # it imports neither chatko nor chatko_briar, and they never import it
tests/
  unit/              # domain and application, with fakes
  contract/          # each extension against extension_api.testing
  integration/       # real SQLite; opt-in docker lab (Mosquitto + meshtasticd, briar-headless)
routing.example.py   # sample routing script, tested in CI like any other code
lab/                 # docker compose lab: Mosquitto + two meshtasticd nodes, plus spike scripts
```

Built-in extensions are shipped in the same repository and distribution for now, but they are registered
exactly like third-party ones: through the `chatko.extensions` entry point group in `pyproject.toml`.
Any of them can move to its own package later without code changes.

### 2.1 The domain

`chatko.domain` is plain, immutable dataclasses and pure functions (D32). It imports only the
standard library, raises one exception type (`DomainError`, a `ValueError`) for values that break
its rules, and re-exports its public names from the package.

| Module | Contents |
|---|---|
| `endpoints` | `EndpointRef(instance, name)`, the name being the admin's (`family.tg`, D34); `Group(name, sites)` with `other_sites(endpoint)` |
| `accounts` | `AccountKey(kind, external_id)` (`telegram:123`, parsed from the config form); `Account(key, display_name, short_name)` as the network shows it now; `Person(label, accounts)`; `Author(account, person, relayed_label)`, where `relayed_label` marks a peer hub's relay (D17) |
| `messages` | `MessageId`; `Attachment(kind)` with its `[photo]` placeholder; `Message` (endpoint, transport id, author, text, attachments, time) with `plain_text` and `fingerprint`; `Target(endpoint, text, label)` |
| `delivery` | `Delivery`: one outbox row (message, endpoint, author label, text, due time, attempts, last error). `delivered`, `retry(at)` and `failed` return a new value; delivered and failed are final |
| `topology` | `Topology(groups, sources, people)`: lookups (`group`, `group_of`, `source`, `person_of`, `author_of`) and the rules that every endpoint is the site of one group or one source, endpoint names are unique, and every account belongs to one person |
| `fingerprint` | `Fingerprint`, `fingerprint(label, text)` and the normalization (design.md §9.5) |
| `labels`, `transliteration` | `default_label(author)`, `generate_label(account)`, `transliterate` (design.md §8) |

The domain does not decide what goes where (routing, invariants, backoff): that is the application
layer's (§5), built on these types.

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

An extension provides endpoints:

| Protocol | Methods (extension side) | Calls into the hub |
|---|---|---|
| `EndpointProvider` | `endpoint_config_model`; `attach_endpoint(endpoint, cfg)`, `detach_endpoint(endpoint)`; `deliver(endpoint, OutboundMessage) → DeliveryResult` | `hub.submit(InboundMessage)` |

The protocol set can grow later (e.g. commands for a web UI) without changing existing extensions.

`HubContext` is the only thing the core gives an extension. It can:
- submit inbound messages;
- post an admin notice (design.md §2);
- reach a namespaced key–value store for the extension's own state (e.g. Briar contact ids, node keys
  seen);
- use a logger and a clock.

Key types:
- `AccountKey(kind, external_id)`, e.g. `("telegram", "123")`, `("meshtastic", "!a1b2c3d4")`, plus the
  display name and, if the network has one, the short name it gives (the domain's `Account`).
- `EndpointRef(instance, name)`: a site or a source, named by the admin in the config (`family.tg`,
  `longfast`; D34). The rest of an endpoint's config is the extension's and is validated by its
  `endpoint_config_model`; the core never reads it. The extension does not know which; groups are a
  core concept.
- `InboundMessage(endpoint, external_id, author: Account, text, attachments)`, where `external_id` is
  used for de-duplication.
- `OutboundMessage(message_id, author_label, text, attachments)`. The core supplies `author_label`
  (design.md §8): from the script's `label` hook, a target override, or `default_label`. Each extension
  renders the final text for its network, e.g. the Meshtastic extension splits it into 200-byte parts.
- `DeliveryResult`: `Delivered` | `Retry(after)` | `Failed(reason)`, plus an optional `truncated`
  flag.

Rules for extension authors:
- Never block the event loop. Wrap thread-based client libraries (e.g. `meshtastic`'s `TCPInterface`)
  in an adapter that bridges to asyncio with `loop.call_soon_threadsafe`. The Meshtastic adapter is the
  only client of its node and owns reconnection: the node closes the connection when it reboots or
  when another client connects, and the library's own reconnect must not race with the adapter's
  (spike S2, D25). It sends admin messages one at a time and waits for each response, matches ACKs and
  NAKs to sent packets by request id itself (the library drops a response handler after the first
  response, which is the node's implicit ACK), and takes node keys from the node database it reads on
  connect and from its own `add_contact` calls, not from the library's node cache, which also takes
  keys from NodeInfo the node rejected (D26).
- The Briar adapter owns the WebSocket to `briar-headless` and its reconnection. After every
  (re)connect it authenticates the WebSocket first and then catches up: it lists the messages of each
  configured group, submits the unread posts of other members and marks each read once `hub.submit`
  has stored it, so that nothing posted while chatko was down is lost (design.md §7.2, D29). It keeps
  Briar ids as bytes and converts between the API's standard base64 (JSON) and URL-safe base64 (URL
  paths, config, logs).
- Talk to the external system through a small internal port (e.g. `TelegramApi`, `BriarApi`), so that
  the extension logic is tested against a fake of that port.
- Drop the hub's own posts before calling `hub.submit`.

## 4. Routing API

What routing does is in [design.md §9](design.md#9-routing). A sketch; the real signatures are
settled in phase 1 together with the extension API.

```python
# config/routing.py
from chatko.routing_api import RoutedMessage, RoutingContext, Target, mirror, to_endpoint

def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    if msg.endpoint == ctx.source("longfast"):                 # a feed
        return [to_endpoint(ctx.endpoint("owner"))]
    return mirror(msg, ctx)                                    # all other sites of the group
```

- `RoutedMessage` (read-only): `endpoint`, `group | None`, `author` (account, display name, person or
  `None`, relayed-by-peer), `text`, `attachments`, `fingerprint`, `received_at`.
- `RoutingContext` (read-only): groups and their sites, sources and other named endpoints, people,
  peers, `last_heard(account, endpoint)`, `seen(fingerprint, within)`, the clock.
- `Target`: `to_endpoint(ref, *, text=None, label=None)`.
- Optional hook `label(author, target, ctx) -> str`; without it the core uses `default_label`.
- The script is synchronous and must not do I/O. The routing engine calls it from the event loop.
- `routing_api.testing` gives a fake installation builder and assertions, so admins test their script
  with plain `pytest`. `routing.example.py` is tested in our CI the same way.

The routing engine (application layer) loads the script from the config directory, validates that it
defines `route`, and keeps the last good version on reload errors. After `route` returns, it applies
the invariants of design.md §9.3 in one place, `RoutingInvariants`, which is covered by property-style
tests (whatever the script returns, no echo and no duplicate delivery).

## 5. Core services (application layer)

| Service | Responsibility |
|---|---|
| `InboundPipeline` | own-post and transport-id dedup → find the person for the account → peer-relayed author → fingerprint → persist message → `RoutingEngine` → invariants → labels → one outbox row per target |
| `RoutingEngine` | load and hot-reload the routing script, call `route` and `label` (or the defaults), fall back to the defaults on errors and post an admin notice |
| `OutboxWorker` | take due rows, call `deliver`, apply `DeliveryResult` with exponential backoff; survives restarts |
| `AdminNotifier` | post admin notices into the configured endpoint, rate-limited and de-duplicated per kind |
| `ConfigService` | load, validate (core + each extension's models) and hot-reload `chatko.yaml`; keep the last valid one |

## 6. Technology

| Area | Choice |
|---|---|
| Language | Python 3.12+, asyncio, full type hints |
| Tooling | `uv` (environments, lock file), `ruff` (lint + format), `mypy --strict`, `import-linter`, `pre-commit` |
| Tests | `pytest`, `pytest-asyncio`, `pytest-cov`/`coverage` (branch); `respx`/local test servers for HTTP fakes |
| Config | `pydantic` v2 models, YAML (`PyYAML`, safe loader), `${ENV}` substitution, `watchfiles` for reload |
| Storage | SQLite via `aiosqlite`, schema migrations in code |
| Telegram | `aiogram` 3, wrapped behind the extension's `TelegramApi` port |
| Meshtastic | official `meshtastic` Python library over TCP to `meshtasticd` (serial, BLE and TCP to a physical node later); image `meshtastic/meshtasticd`, tag pinned in the compose files |
| MQTT broker | Mosquitto 2 in the compose files: users and an ACL per hub node and gateway, TLS on 8883 for gateways (D30). The hub's code never talks MQTT itself; its `meshtasticd` nodes do |
| Briar | `httpx` + `websockets` to `briar-headless` (our fork: a pinned upstream tag plus the private-group patch, D29; built with JDK 17, run in a Java 17 JRE image, D28) |
| License | GPL-3.0-or-later |

## 7. Quality gates

Every pull request must pass in CI (GitHub Actions; Linux, macOS and Windows for the hub):

- `ruff check`, `ruff format --check`;
- `mypy --strict`;
- `lint-imports` (the dependency rule);
- `pytest` with branch coverage: **≥ 95 %** for `domain`, `application` and `routing_api`, **≥ 85 %** for each
  extension, **≥ 90 %** overall;
- the contract test suite for every built-in extension.

The checks are configured in `pyproject.toml`, run by `.github/workflows/ci.yml` and, except for the
coverage gates, by `pre-commit` (`.pre-commit-config.yaml`). The coverage gates are separate
`coverage report --include=… --fail-under=…` steps in the workflow; a package without code reports
100 %. `lab/` (spike scripts) and `docs/` are outside `ruff` and `mypy`.

The `import-linter` contracts (`[tool.importlinter]`) are:
- layers `app > infrastructure > application > domain`, and `domain` imports nothing outward;
- `extension_api` and `routing_api` do not import the infrastructure or `app`, and not each other;
- the core never imports an extension; extensions import `chatko.extension_api` only (through it
  they may reach the domain indirectly) and not each other;
- `briarctl` imports nothing of chatko and chatko never imports it.

Whether `application` may import the two public APIs (the diagram of §1 puts them outside it) is left
open until their real shape is settled in S10.

Integration tests with the docker lab run on demand and nightly; they are not required for every PR.

Testing style:
- Test behaviour through public APIs, not private helpers. One behaviour per test, with a descriptive
  name.
- Unit tests never touch the network, the real clock or the filesystem outside `tmp_path`.
- Every bug fix starts with a failing test.
- Fakes (in-memory repositories, `FakeExtension`, fake ports) live in the code base next to the ports
  they fake, not as ad-hoc mocks in tests.
