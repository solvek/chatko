# Architecture

How the code is organized. What the system does is in [design.md](design.md).

## 1. Principles

1. **Clean architecture with a strict dependency rule.** Each layer imports only the layers below
   it (D35):

   ```
   app, infrastructure              composition root, CLI, SQLite, config, extension discovery
           │
   application                      use cases and ports; reaches the extensions and the routing
           │                        script only through the two public APIs
   extension_api  │  routing_api    the public APIs, independent  ◄── extensions (telegram, …)
           │                        of each other                 ◄── the routing script
   domain                           entities and pure rules
   ```

   - `domain` knows nothing about I/O, asyncio, databases or networks.
   - The public APIs (`extension_api`, `routing_api`) import only the domain. They define what the
     application calls (`EndpointProvider.deliver`, the script's `route`) and what it implements
     for the plug-ins (`HubContext`, the routing context's history), so the application depends on
     them and never the other way round. Their test kits (`testing` subpackages) are for tests only.
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
    extension_api/   # the ONLY package extensions may import (§3): extension (Extension), endpoints
                     # (EndpointProvider), hub (HubContext), messages, delivery (results, reports)
      testing/       # contract (the suite), hub (FakeHub), fake (FakeNetwork, FakeExtension)
    routing_api/     # the ONLY package a routing script may import (§4): messages (RoutedMessage),
                     # context (RoutingContext, RoutingHistory), helpers (to_endpoint, mirror, …)
      testing/       # FakeHistory; the fake installation and assertions come in S12
    infrastructure/  # SQLite repositories, config loading (YAML + ${ENV}), extension discovery,
                     # logging
    app/             # composition root, CLI: `chatko run`, `chatko check-config`
  chatko_telegram/   # extension packages: separate top-level packages, so importing core internals
  chatko_meshtastic/ # is visible and forbidden by import-linter
  chatko_briar/
  briarctl/          # the admin's CLI for the hub's Briar account (design.md §7.5); a separate program:
                     # it imports neither chatko nor chatko_briar, and they never import it
tests/
  unit/              # domain, the public APIs and application, with fakes
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
| `messages` | `MessageId`; `Attachment(kind)` with its `[photo]` placeholder; `plain_text(text, attachments)`; `Message` (endpoint, transport id, author, text, attachments, time) with `plain_text` and `fingerprint`; `Target(endpoint, text, label, recipients)`, where `recipients` narrows a delivery to some of the endpoint's recipients |
| `delivery` | `Delivery`: one outbox row (message, endpoint, recipient, author label, text, due time, attempts, last error). `delivered`, `retry(at)` and `failed` return a new value; delivered and failed are final |
| `topology` | `Topology(groups, sources, people)`: lookups (`endpoint` by name, `group`, `group_of`, `source`, `person_of`, `author_of`) and the rules that every endpoint is the site of one group or one source, endpoint names are unique, and every account belongs to one person |
| `fingerprint` | `Fingerprint`, `fingerprint(label, text)` and the normalization (design.md §9.5) |
| `labels`, `transliteration` | `default_label(author)`, `generate_label(account)`, `transliterate` (design.md §8) |

The domain does not decide what goes where (routing, invariants, backoff): that is the application
layer's (§5), built on these types.

## 3. Extension API

`chatko.extension_api` (D35). It re-exports the domain types an extension needs (`EndpointRef`,
`Account`, `AccountKey`, `Attachment`, `AttachmentKind`, `MessageId`), so an extension imports
nothing else of chatko.

```python
class TelegramExtension(Extension[TelegramConfig], EndpointProvider[TelegramChat]):
    type_name = "telegram"                  # `type:` in the config, and the kind of its accounts
    api_version = (1, 0)                    # the version of this API it was written for (§3.4)
    config_model = TelegramConfig           # pydantic models that reject unknown keys; the core
    endpoint_config_model = TelegramChat    # validates the sections without `type` and `ext`

    def __init__(self, instance, config, hub, *, api=None): ...    # no I/O; a fake port for tests
    def set_endpoints(self, endpoints: Mapping[EndpointRef, TelegramChat]) -> None: ...
    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]: ...      # optional: ()
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def deliver(self, endpoint: EndpointRef, message: OutboundMessage) -> DeliveryResult: ...
    async def delivery_report(self, report: DeliveryReport) -> None: ...      # optional: ignore
```

`Extension[C]` is the lifecycle; `EndpointProvider[E]` is the one capability of v1 (D22). The base
class keeps `instance`, `config`, `hub` and a logger `chatko.extensions.<instance>`. An extension is
registered in the `chatko.extensions` entry point group under its `type_name`.

### 3.1 Lifecycle

1. **Discovery.** The core loads the class from its entry point and refuses it unless
   `is_supported(api_version)` (§3.4).
2. **Validation.** `config_model` validates the instance's section, `endpoint_config_model` each
   endpoint's (D34: the core reads only `type` and `ext`).
3. **Construction** `cls(instance, config, hub)`, then **`set_endpoints`** with the instance's
   endpoints in config order. Neither does I/O or starts tasks, so the core can check a config by
   running these two steps on a fresh instance, as `chatko check-config` and every reload do;
   `set_endpoints` raises `ValueError` for a set that cannot work (e.g. a channel the node lacks).
4. **`start`** starts the background work and returns without waiting for the network. It raises
   only when the instance cannot work at all; the other instances keep running.
5. **Running.** The hub calls `deliver`, `delivery_report`, and `set_endpoints` when the endpoints
   change (with a set already checked on a fresh instance); the extension calls the hub.
6. **`stop`**, after the deliveries in progress have ended: cancel the tasks, close the
   connections; no hub calls afterwards. It is safe without `start` and after a failed `start`.

When an instance's own section changes, the core stops it and starts a new instance (steps 2–4).

### 3.2 Endpoints and deliveries

- `set_endpoints(endpoints)` gives the complete set, ordered as in the config; the extension applies
  the difference, reads only those endpoints and ignores the rest of its network (a foreign
  Telegram group, a node in no `dm` list). The order lets an extension pick "the first" endpoint, as
  the Meshtastic extension does for a node listed in several `dm` endpoints (design.md §6.2).
- **Recipients.** An endpoint that reaches several addressees separately (the nodes of a Meshtastic
  `dm` endpoint) lists them in `recipients(endpoint)`. The core then makes one delivery per
  recipient: each is sent, confirmed, retried and reported on its own, and one node that is away does
  not hold up the others. An endpoint that is one place (a chat, a channel, a Briar group) has none.
- **Order.** For one endpoint and recipient, the core makes one `deliver` call at a time, oldest
  message first; different endpoints and recipients are served concurrently. A delivery waiting
  for its retry holds back the newer ones, so messages arrive in the order they were written.
- **Results.** `deliver` returns:

  | Result | Meaning | The core |
  |---|---|---|
  | `Delivered(truncated=False)` | the endpoint (recipient) has it; `truncated`: only part fit | marks it delivered |
  | `Retry(reason, after=None)` | not now: the network is down, busy or did not confirm | tries again after `after`, or after its exponential backoff; holds back newer messages meanwhile; `hub.retry_now` ends the wait |
  | `Failed(reason)` | trying again cannot help: the chat is gone, the endpoint or recipient is no longer in the config | marks it failed and logs it |

  An exception from `deliver` counts as `Retry` and is logged. Delivery is at least once: after a
  crash an attempt may be repeated, and `OutboundMessage.attempt` tells a repeat from the first.
- **Reports.** When a delivery has ended, the core tells the extension that read the original:
  `delivery_report(DeliveryReport(source, transport_id, target, recipient, result))`. The Telegram
  extension shows a truncated delivery with a ✂️ reaction (design.md §6.3). The default ignores it.

| Type | Fields |
|---|---|
| `InboundMessage` | `endpoint`, `transport_id` (unique within the endpoint, the same each time the network hands over the same message), `author: Account` (its kind is the extension's `type_name`), `text`, `attachments` |
| `OutboundMessage` | `message_id`, `author_label`, `text`, `received_at` (when the hub got the original), `attachments`, `recipient`, `attempt`; `plain_text` (`[photo] caption`) and `formatted` (`NatAda: [photo] caption`). The core chose the label (design.md §8) and the text (a target may replace it); the extension renders them for its network, shortening a label that is too long and splitting a long text |
| `DeliveryResult` | `Delivered(truncated)` \| `Retry(reason, after)` \| `Failed(reason)` |
| `DeliveryReport` | `source`, `transport_id`, `target`, `recipient`, `result` (`Delivered` or `Failed`) |

### 3.3 `HubContext`

The only object of the core an extension sees. It is called from the extension's event loop, and
only while the instance runs.

| Method | What it does |
|---|---|
| `await submit(InboundMessage)` | hands over a message read at one of the extension's endpoints; returns once the hub has stored it or recognized it as a copy, so only then may the extension confirm it to its network (a Briar post marked read). Safe to cancel; a resubmission is de-duplicated by the transport id. It never calls back into the extension |
| `await heard(account, endpoint=None)` | the network showed the account to be there, at the endpoint if it was heard at one (any packet from a radio counts); the routing script reads it as `last_heard`. Submitted messages count without it |
| `await retry_now(endpoint, recipient=None)` | the deliveries waiting for a retry there are due now, e.g. when a radio that missed messages is heard again (D26) |
| `await notify_admin(text, *, key=None)` | an admin notice (design.md §2); notices with the same key (default: the text) are rate-limited together; never raises |
| `now()` | the hub's clock, timezone-aware |

The architecture once had a key–value store for an extension's own state here. No v1 extension
needs one (D24 took contacts out of the Briar extension; recipients took the per-node delivery state
out of the Meshtastic one), so it is left out until one does; adding it is a minor version.

### 3.4 Versioning

`API_VERSION` is `(major, minor)`, now `(1, 0)`. A minor version only adds: a new class or field
with a default at the end, an optional method with a default on `Extension` or `EndpointProvider`,
a method on `HubContext` (which only the core implements), a new capability. A major version
changes or removes something. An extension declares the version it was written for, and
`is_supported(version)` accepts the same major version with a minor version no newer than the
core's. The core refuses an extension that is not supported, with a message naming both versions.

More capabilities can come later without touching existing extensions (D22): each is a base class
next to `EndpointProvider` (e.g. commands for a web UI), and the core checks which ones an
extension derives from.

### 3.5 The contract test suite

`chatko.extension_api.testing` is the test kit every extension uses:

- `ExtensionContract`: the contract suite. An extension's test module subclasses it as
  `TestXxxContract` and returns a `ContractDriver` from `make_driver`; pytest collects the inherited
  tests. The driver connects the suite to the extension and to the fake of its network port: a
  valid instance and endpoint config, `create`, `receive` (the network hands the extension a post,
  by someone else or by the hub's own account), `receive_again`, `posted`, `go_offline`, `settle`.
- `FakeHub`: records every hub call, with a clock that stands still, and waits for submissions.
- `FakeNetwork` and `FakeExtension`: an in-memory network and the reference extension for it. The
  suite checks itself against it (`tests/contract/test_fake_extension.py`), and the core's tests use
  it in place of real networks.

The suite checks, without a network, that an extension: has a valid `type_name` and a supported
`api_version`; provides endpoints; rejects unknown config keys; starts and stops without leaving
tasks behind, and stops safely without a start; submits what someone posted at one of its
endpoints, with the right endpoint, text, author kind and a transport id that is unique per post and
stable when the network hands a post over again; never submits the hub's own posts, posts at other
places, at an endpoint removed by `set_endpoints`, or after `stop`; follows a new endpoint set;
delivers the label and the text to every recipient; answers `Retry` while the network is down and
`Failed` for an endpoint or recipient it does not have; and takes delivery reports.

### 3.6 Rules for extension authors

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
  the extension logic is tested against a fake of that port, and the contract suite runs against it.
  Create the port's connection in `start`, not in the constructor.
- Drop the hub's own posts before calling `hub.submit`.
- Pass the contract suite (§3.5).

## 4. Routing API

What routing does is in [design.md §9](design.md#9-routing). `chatko.routing_api` (D36) re-exports
the domain types a script needs (`EndpointRef`, `Group`, `Account`, `AccountKey`, `Person`,
`Author`, `Attachment`, `AttachmentKind`, `Fingerprint`, `MessageId`, `Target`).

```python
# config/routing.py
from chatko.routing_api import RoutedMessage, RoutingContext, Target, default_label, mirror, to_endpoint

api_version = (1, 0)                                          # optional

def route(msg: RoutedMessage, ctx: RoutingContext) -> list[Target]:
    if msg.endpoint == ctx.source("longfast"):                # a feed
        return [to_endpoint(ctx.source("owner"))]
    return mirror(msg, ctx)                                   # all other sites of the group

def label(msg: RoutedMessage, target: Target, ctx: RoutingContext) -> str:   # optional
    return default_label(msg.author, ctx)
```

| Name | What |
|---|---|
| `RoutedMessage` | the message, read-only: `id`, `endpoint`, `group` (`None` for a source), `author` (`Author`: the account with its names, the person or `None`, a peer's `relayed_label`), `text`, `attachments`, `plain_text`, `fingerprint`, `received_at` |
| `RoutingContext` | the installation, read-only: `now`, `groups`, `sources`, `people`; `group(name)`, `group_of(endpoint)`, `source(name)`, `endpoint(name)` (`family.radio`), `person_of(account)`, `extension_type(endpoint)` (`meshtastic`), `recipients(endpoint)`; `last_heard(account, endpoint=None)`, `seen(fingerprint, within=…)` |
| `to_endpoint(endpoint, *, text=None, label=None, recipients=None)` | a `Target`. `text` and `label` replace the message's text and the author label at that endpoint; `recipients` narrows the delivery to some of the endpoint's recipients (an empty list: none) and has no effect on an endpoint without recipients |
| `mirror(msg, ctx)` | the default router: targets for all other sites of the message's group, none for a source |
| `default_label(author, ctx)` | the label the core uses without a hook (design.md §8) |
| `RouteFunction`, `LabelFunction` | the types of `route` and `label` |
| `RoutingHistory` | what `last_heard` and `seen` read; implemented by the core and by the test kit |
| `API_VERSION`, `is_supported` | as in §3.4 |

- The script is synchronous and must not do I/O. The routing engine calls it from the event loop,
  with a new context per message. The context serves everything from memory: `last_heard` and
  `seen` read a `RoutingHistory` the core keeps in memory, and `seen` counts only the messages
  stored before the one being routed.
- The `label` hook gets the message, not only its author, so that a label may depend on where the
  message came from (a feed from `longfast` signed `[BC1] Base Camp`). The core calls it for every
  target that does not carry its own label, after the invariants.
- A script may declare `api_version`; the engine refuses a script whose version is not supported
  (the defaults run, and the admin gets a notice). Without it, the script is taken to be current.
- Room for peers (D17): `Author.relayed_label` is there now; `ctx.peers` comes with the second hub,
  as a minor version.
- `routing_api.testing` has `FakeHistory` now; S12 adds a fake installation builder and assertions,
  so admins test their script with plain `pytest`. `routing.example.py` is tested in our CI the same
  way.

The routing engine (application layer) loads the script from the config directory, validates that it
defines `route`, and keeps the last good version on reload errors. After `route` returns, it applies
the invariants of design.md §9.3 in one place, `RoutingInvariants`, which is covered by property-style
tests (whatever the script returns, no echo and no duplicate delivery). The invariants hold per
endpoint and recipient: a message goes to each at most once, and never back to its own endpoint.

## 5. Core services (application layer)

| Service | Responsibility |
|---|---|
| `InboundPipeline` | own-post and transport-id dedup → find the person for the account → peer-relayed author → fingerprint → persist message → `RoutingEngine` → invariants → labels → one outbox row per target and recipient |
| `RoutingEngine` | load and hot-reload the routing script, check its `api_version`, call `route` and `label` (or the defaults), fall back to the defaults on errors and post an admin notice |
| `OutboxWorker` | per endpoint and recipient, deliver the oldest pending row when it is due; a `Retry` holds back the newer rows (§3.2); apply `DeliveryResult` (the extension's `after`, else exponential backoff); report final results to the source's extension; survives restarts |
| the hub (`HubContext`) | one per extension instance: `submit` into the `InboundPipeline`, `heard` into the last-heard state, `retry_now` into the outbox, `notify_admin` into the `AdminNotifier` |
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
- `pytest` with branch coverage: **≥ 95 %** for `domain`, `application`, `extension_api` and
  `routing_api`, **≥ 85 %** for each extension, **≥ 90 %** overall;
- the contract test suite for every built-in extension.

The checks are configured in `pyproject.toml`, run by `.github/workflows/ci.yml` and, except for the
coverage gates, by `pre-commit` (`.pre-commit-config.yaml`). The coverage gates are separate
`coverage report --include=… --fail-under=…` steps in the workflow; a package without code reports
100 %. `lab/` (spike scripts) and `docs/` are outside `ruff` and `mypy`.

The `import-linter` contracts (`[tool.importlinter]`) are:
- layers `app > infrastructure > application > extension_api | routing_api > domain` (§1), the two
  APIs being independent siblings;
- only tests import the test kits (`extension_api.testing`, `routing_api.testing`): no layer of the
  core and no extension does;
- the core never imports an extension; extensions import `chatko.extension_api` only (through it
  they may reach the domain indirectly) and not each other;
- `briarctl` imports nothing of chatko and chatko never imports it.

Integration tests with the docker lab run on demand and nightly; they are not required for every PR.

Testing style:
- Test behaviour through public APIs, not private helpers. One behaviour per test, with a descriptive
  name.
- Unit tests never touch the network, the real clock or the filesystem outside `tmp_path`.
- Every bug fix starts with a failing test.
- Fakes (in-memory repositories, `FakeExtension`, fake ports) live in the code base next to the ports
  they fake, not as ad-hoc mocks in tests.
