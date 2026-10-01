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
    application/     # use cases and ports (§5): pipeline, invariants, outbox (worker), hub
                     # (HubContext), history, installation, routing (the router), config,
                     # config_service, notifier, accounts, runtime (HubRuntime), ports
      testing/       # in-memory fakes of the ports: FakeClock, SequentialIds, InMemoryStore, …
    extension_api/   # the ONLY package extensions may import (§3): extension (Extension), endpoints
                     # (EndpointProvider), hub (HubContext), messages, delivery (results, reports)
      testing/       # contract (the suite), hub (FakeHub), fake (FakeNetwork, FakeExtension)
    routing_api/     # the ONLY package a routing script may import (§4): messages (RoutedMessage),
                     # context (RoutingContext, RoutingHistory), helpers (to_endpoint, mirror, …),
                     # script (RoutingScript, ScriptError), version (API_VERSION, is_supported)
      testing/       # installation (FakeInstallation, RouteResult, assert_routed_to), history
                     # (FakeHistory)
    infrastructure/  # the real clock and ids, the routing script's file, SQLite repositories,
                     # config (YAML + ${ENV}), the file watcher, extension discovery
    app/             # cli, run (the composition root of `chatko run`), check_config
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
| `messages` | `MessageId`; `Attachment(kind)` with its `[photo]` placeholder; `plain_text(text, attachments)`; `Message` (endpoint, transport id, author, text, attachments, time, `from_recipient`) with `plain_text` and `fingerprint`; `Target(endpoint, text, label, recipients)`, where `recipients` narrows a delivery to some of the endpoint's recipients |
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

When an instance's own section changes, the core stops it and starts a new instance (steps 2–4);
so it does with an instance whose `start` failed, at the next config that loads. To stop an
instance while the hub runs, the core first gives it no new deliveries and no new delivery
reports, waits for the calls into it in progress (each ends within the call timeout), and only
then calls `stop` (§5.5).

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
  | `Retry(reason, after=None)` | not now: the network is down, busy or did not confirm | tries again after `after`, or after its exponential backoff; holds back newer messages meanwhile; `hub.retry_now` ends the wait; gives up (`Failed`) once the message is too old (§5.2) |
  | `Failed(reason)` | trying again cannot help: the chat is gone, the endpoint or recipient is no longer in the config | marks it failed and logs it |

  An exception from `deliver` counts as `Retry` and is logged; so does a call that takes longer than
  the call timeout, and a delivery to an instance that is not running. Delivery is at least once:
  the core counts each attempt in the outbox before it calls `deliver`, so after a crash an attempt
  may be repeated, and `OutboundMessage.attempt` tells the repeat from the first.
- **Reports.** When a delivery has ended, the core tells the extension that read the original:
  `delivery_report(DeliveryReport(source, transport_id, target, recipient, result))`. The Telegram
  extension shows a truncated delivery with a ✂️ reaction (design.md §6.3). The default ignores it.

| Type | Fields |
|---|---|
| `InboundMessage` | `endpoint`, `transport_id` (unique within the endpoint, the same each time the network hands over the same message), `author: Account` (its kind is the extension's `type_name`), `text`, `attachments`, `from_recipient` (at an endpoint with recipients: the one that posted it, e.g. the node that sent the direct message; the hub relays the message to the endpoint's other recipients, never back to this one) |
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
| `await notify_admin(text, *, key=None)` | an admin notice (design.md §2); notices of the instance with the same key (default: the text) are rate-limited together; never raises |
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
endpoints, with the right endpoint, text, author kind, a transport id that is unique per post and
stable when the network hands a post over again, and at an endpoint with recipients the recipient
that posted it; never submits the hub's own posts, posts at other
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
- Give a `Retry` an `after` only when the network says when to try again: the core waits exactly
  that long, so a zero wait repeats the attempt at once.
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
| `RoutedMessage` | the message, read-only: `id`, `endpoint`, `group` (`None` for a source), `author` (`Author`: the account with its names, the person or `None`, a peer's `relayed_label`), `from_recipient` (the recipient of the endpoint that posted it, or `None`), `text`, `attachments`, `plain_text`, `fingerprint`, `received_at` |
| `RoutingContext` | the installation, read-only: `now`, `groups`, `sources`, `people`; `group(name)`, `group_of(endpoint)`, `source(name)`, `endpoint(name)` (`family.radio`), `person_of(account)`, `extension_type(endpoint)` (`meshtastic`), `recipients(endpoint)`; `last_heard(account, endpoint=None)`, `seen(fingerprint, within=…)` |
| `to_endpoint(endpoint, *, text=None, label=None, recipients=None)` | a `Target`. `text` and `label` replace the message's text and the author label at that endpoint; `recipients` narrows the delivery to some of the endpoint's recipients (an empty list: none) and has no effect on an endpoint without recipients |
| `mirror(msg, ctx)` | the default router: targets for all other sites of the message's group, and for a message from one recipient of a site, the site narrowed to its other recipients; none for a source |
| `default_label(author, ctx)` | the label the core uses without a hook (design.md §8) |
| `RouteFunction`, `LabelFunction` | the types of `route` and `label` |
| `RoutingHistory` | what `last_heard` and `seen` read; implemented by the core and by the test kit |
| `RoutingScript.of(script)`, `ScriptError` | the checked functions of a loaded script (a module): `route`, `label` or `None`, `api_version`. Raises `ScriptError`, with a reason for the admin, unless the script defines `route(msg, ctx)`, a `label` that takes `(msg, target, ctx)` if any, and a supported `api_version` if any (checked first: a script for another major version may define other functions). The engine and the test kit both check scripts with it |
| `API_VERSION`, `is_supported` | as in §3.4 |

- The script is synchronous and must not do I/O. The routing engine calls it from the event loop,
  with a new context per message. The context serves everything from memory: `last_heard` and
  `seen` read a `RoutingHistory` the core keeps in memory, and `seen` counts only the messages
  stored before the one being routed.
- The `label` hook gets the message, not only its author, so that a label may depend on where the
  message came from (a feed from `longfast` signed `[BC1] Base Camp`). The core calls it for every
  target that does not carry its own label, after the invariants.
- A script may declare `api_version` as a pair of numbers; the engine refuses a script whose
  version is not supported (the previous version or the defaults run, and the admin gets a
  notice). Without it, the script is taken to be current.
- Room for peers (D17): `Author.relayed_label` is there now; `ctx.peers` comes with the second hub,
  as a minor version.
- `routing_api.testing` is the admin's test kit (design.md §9.5): `FakeInstallation(extensions=…,
  groups=…, sources=…, recipients=…, people=…, now=…)` takes the installation in the shape of
  `chatko.yaml` (a site is `{site name: extension instance}`); `message(endpoint, text, author=…,
  name=…, short_name=…, attachments=…, from_recipient=…, relayed_label=…)`, `hear`, `see`,
  `context()`; `route(script, msg)` checks the script with `RoutingScript.of`, runs `route` and
  labels each target as the core does, and returns a `RouteResult` (`endpoints`, `to(name)` →
  `Outgoing`: `label`, `text`, `recipients`, `formatted`, `target`), raising the script's errors.
  It does not apply the invariants, which are the application's; `assert_routed_to(result, *names)`
  compares the endpoints in any order. `routing.example.py` is type-checked with `mypy --strict`
  and tested this way in CI (`tests/examples/test_routing_example.py`, the template for an admin's
  `config/test_routing.py`).

The routing engine (application layer, §5) runs the script; the pipeline applies the invariants of
design.md §9.3 to whatever it returns in one place, `RoutingInvariants`, which is covered by
property-style tests (whatever the script returns, no echo and no duplicate delivery). The invariants hold per
endpoint and recipient: a message goes to each at most once, and never back to where it came from
(its endpoint, or only the recipient that posted it when the extension names it, D38).

## 5. Core services (application layer)

`chatko.application` (D37). The services take their ports and collaborators as keyword arguments;
`HubRuntime` wires them over the ports, and `app.run`, the composition root, puts the
infrastructure under it (§5.5, D42).

| Service | Module | Responsibility |
|---|---|---|
| `InboundPipeline` | `pipeline` | for each submitted message, one at a time: drop it if its endpoint is unknown or it is a copy (same endpoint and transport id) → find the person for the account → (later: the peer-relayed author) → fingerprint → skip routing if the endpoint de-duplicates by fingerprint and the same message came lately → `Router.route` → `RoutingInvariants` → `Router.label` once per target without its own label → store the message with one outbox row per target and recipient, in one transaction → record it in the history → hand the rows to the `OutboxWorker`. Returns an `Outcome`; `drain()` waits for the messages in hand |
| `RoutingInvariants` | `invariants` | turns the targets into `Destination`s (target, recipient) that keep design.md §9.3: none back at the source (only at the recipient that posted it, if the extension named it, D38), none at an unknown endpoint, each endpoint and recipient once (the first target that includes it wins), only the endpoint's own recipients |
| `Router` | `routing` | the port the pipeline routes with: `route` and `label`, synchronous and never raising. `DefaultRouter`: `mirror` and `default_label` |
| `RoutingEngine` | `routing` | the `Router` of a running hub (§5.3): the routing script, or `DefaultRouter` without one. `reload()` reads the script from a `RoutingScriptSource`, loads it with `load_script` and keeps the last good version; `route` and `label` fall back to the defaults on a script's errors (an `exit()` in it too) and post an admin notice once per error kind |
| `OutboxWorker` | `outbox` | delivers the pending rows, one lane per endpoint and recipient (§5.2); reports final results to the source's extension; loads the pending rows on `start`; `finish_attempts(instance)` waits for the calls into an instance in progress |
| `ExtensionHub` | `hub` | the `HubContext` of one extension instance: `submit` into the pipeline, `heard` into the history, `retry_now` into the worker, `notify_admin` into `AdminNotices` (with the instance in the key), `now` from the clock; the authors of stored messages and the accounts heard go to `NewAccounts`. It ignores (and logs) calls about another instance's endpoints |
| `NewAccounts` | `accounts` | notes every account an extension shows in the `AccountRegistry`; for a key seen for the first time that belongs to no person, posts a notice (key `account:<key>`) if `admin_notices.new_accounts` is on (design.md §8). Never raises |
| `HubHistory` | `history` | the routing script's `RoutingHistory`, in memory: the last time each account was heard (anywhere and per endpoint) and the fingerprints of the last 7 days |
| `HistoryPersistence` | `history` | keeps a `HubHistory` in a `HistoryRepository`: `load(now)` at start, `flush()` saves what `HubHistory.take_unsaved()` hands over (kept for the next flush if the repository fails), `prune(now)` drops arrivals past the retention. The `HubRuntime` flushes every 10 s and at stop, and prunes with the messages at start and daily |
| `Installation` | `installation` | one consistent snapshot of the topology, the extension instances (`running` names the started ones: only they get deliveries and reports, while the recipients and types of all are known), the endpoints that de-duplicate by fingerprint and the admin endpoint; the services ask an `InstallationSource` for the current one per message, notice and attempt, so a reload only swaps the snapshot |
| `AdminNotifier` | `notifier` | implements `AdminNotices`: stores a notice as a message from `NOTICE_SOURCE` with one delivery per recipient of the admin endpoint, both from the current snapshot (D43), rate-limited per key; it only logs without an endpoint and never raises (D41) |
| `validate_config`, `Config` | `config` | the models of `chatko.yaml` and the validation: core sections, each extension's and endpoint's models, topology, the check of every instance on a fresh object (§5.4) |
| `ConfigService` | `config_service` | `reload()` loads and validates through a `ConfigLoader` and keeps the last valid `Config` (`current`), reporting a refusal once with the key `config:load` (D41) |
| `HubRuntime` | `runtime` | the running hub (§5.5): builds the services above over `HubPorts`, runs the extension instances of the config, applies reloads (`refresh`), saves the history and prunes |

### 5.1 Ports

`application.ports` defines what the application needs from outside; `infrastructure` implements
them and `application.testing` fakes them in memory (`FakeClock`, `SequentialIds`,
`InMemoryStore`, `RecordingNotices`, `InMemoryScriptSource`).

| Port | Methods | Implementations |
|---|---|---|
| `Clock` | `now()`, `await sleep_until(when)` | `SystemClock`; `FakeClock` (moved by the test, wakes the sleepers that are due) |
| `IdGenerator` | `new_message_id()` | `RandomIds` (UUID4 hex); `SequentialIds` (`m1`, `m2`, …) |
| `MessageRepository` | `contains(endpoint, transport_id)`, `add(message, deliveries) -> bool` (both in one transaction; `False` and nothing stored for a copy), `get(id)`, `prune(before) -> int` (old messages without a pending delivery go, with their rows) | `SqliteStore`; `InMemoryStore` |
| `OutboxRepository` | `pending()` (in the order stored), `save(delivery)` | `SqliteStore`; `InMemoryStore` |
| `HistoryRepository` | `load(since) -> HistoryChanges`, `record(changes)` (one transaction; a last-heard time never moves back), `prune(before)` (arrivals only) | `SqliteHistory`; `InMemoryHistoryStore` |
| `AccountRegistry` | `note(account, at) -> bool` (`True` the first time the key is seen; names and last-seen time are updated) | `SqliteAccounts`; `InMemoryAccounts` |
| `AdminNotices` | `notify(text, key=…)` | `AdminNotifier`; `RecordingNotices` |
| `ConfigLoader` | `await load() -> Mapping` (`OSError`; `ConfigError` for a file that is not UTF-8, bad YAML or an unset variable) | `FileConfigLoader(path, env)` (`infrastructure.config`); `InMemoryConfigLoader` |
| `RoutingScriptSource` | `origin`, `await read() -> str \| None` (`None`: no script; `OSError` or `UnicodeDecodeError`: one that cannot be read) | `FileScriptSource(path)` (`config/routing.py`, read in a thread); `ConfiguredScriptSource(directory, name)`, the file the current config names (§5.5); `InMemoryScriptSource` |

SQLite (`chatko.infrastructure.sqlite`, D40): `Database` is one `aiosqlite` connection whose every use is
a transaction under a lock, rolled back if it raises or is cancelled, also while it begins or
commits (D43); its schema is the tuple `MIGRATIONS`, applied in order and numbered by
`PRAGMA user_version` (a database from a newer chatko is refused with `SchemaError`).
`SqliteStore`, `SqliteHistory` and `SqliteAccounts` implement the ports above. The same repository
tests (`tests/integration/test_repositories.py`) run against these and against the in-memory fakes.

The message is routed before it is stored and stored together with its outbox rows, so a crash
leaves either both or neither: no message is stored without its deliveries. `submit` runs the
pipeline in a task of its own, so cancelling the caller does not cut it short.

### 5.2 The outbox worker

- **Lanes.** The pending deliveries to one endpoint and recipient form a lane, oldest first, with a
  task of its own while it has any. The task attempts the oldest delivery once it is due; until
  then the newer ones wait. Lanes run concurrently.
- **An attempt** counts itself in the outbox (`Delivery.begin_attempt`), calls `deliver` with a
  timeout, applies the result and saves it, and then reports a final one to the extension of the
  message's source (`delivery_report`, with the same timeout; its errors are logged).
- **Retries.** A `Retry` is due after its `after`, else after the backoff: 10 s, doubling up to
  1 h (and staying there, however many attempts). Once the message arrived 3 days ago or earlier,
  the next `Retry` makes the delivery fail ("gave up: …"). `retry_now` makes the oldest delivery
  of the matching lanes due at once. A broken repository is logged and the lane tries again after
  the first backoff. `OutboxSettings` holds these numbers; they are not in the config.
- **Endpoints that are gone.** A delivery to an endpoint that is no longer in the topology fails
  ("… is no longer in the config"), so a reload that removes or renames an endpoint drops its
  pending deliveries (D34). A delivery report goes to the source's extension only if it runs when
  the delivery ends.
- **Lifecycle.** `start` loads the pending deliveries (deliveries enqueued meanwhile follow them,
  without duplicates); `enqueue` adds the ones the pipeline stored (while stopped it leaves them to
  the next `start`, which finds them in the outbox); `stop` cancels the waiting lanes and gives the
  deliveries in progress 10 s. `finish_attempts(instance)` waits for the calls into one instance
  in progress, the attempts to its endpoints and the delivery reports to it, so that the instance
  can be stopped. The `HubRuntime` starts the worker
  after the extensions and stops it before them (D42), so the worker calls only started instances.

### 5.3 The routing engine

- **Loading.** `load_script(code, origin)` compiles the code with `origin` (the file's path) as
  its file name, runs it as a fresh module (`chatko_routing_script`, in `sys.modules` only while
  it runs, so that it can define dataclasses), and checks it with `RoutingScript.of` (§4). Any
  error becomes a `ScriptError` with the script's line: `line 3: NameError: …`.
- **Reloading.** `reload()` is called at start and whenever the file may have changed (the
  config watcher through `HubRuntime.refresh`, §5.5). It returns a `ReloadOutcome`: `LOADED`;
  `DEFAULTS` (no script, or it was removed); `REFUSED` (unreadable or not loadable, including a
  script that calls `exit()` while it loads: the previous version, or the defaults, keep running,
  and an admin notice says why, with the key `routing:load`); `UNCHANGED` (the same code as last read, also for a refused one, so a refusal
  is reported once). It reads the file in a thread, so it does not block the event loop.
- **Running.** `route` and `label` call the script synchronously, from the pipeline (§4). A
  `route` that raises (an `exit()` included), or returns `None`, a single `Target`, a string or anything else that is not
  an iterable of `Target`s, is an error: the message goes by `mirror`. A `label` that raises or
  returns anything but a non-blank string is an error: the target gets `default_label`. The kind
  of an error is the function, the exception type and the script line of the innermost frame in
  the script; the first error of a kind is logged with its traceback and posted as an admin notice
  (key `routing:<function>:<type>:<line>`), later ones are logged on one line. A new version of
  the script forgets the reported kinds.
- **Notices without waiting.** `route` and `label` run inside the pipeline's lock, so the engine
  posts their notices in tasks of its own instead of awaiting them: an admin notifier that goes
  through the pipeline (S14) cannot deadlock it. So does `reload` with its refusals: during a
  config reload the task runs after the snapshot that the runtime publishes in the same step as
  the script's swap, so the notice goes to that snapshot's admin endpoint (D43). Lost notices are
  logged.
- A script that never returns blocks the hub: it is trusted code, like the config (design.md §9.5).

### 5.4 The config

- **Loading.** `infrastructure.config.parse_config(text, env)` is `yaml.safe_load` plus `${NAME}`
  substitution in values (D41). `FileConfigLoader` reads the file in a thread; a file that is not
  UTF-8 is a `ConfigError` with the line of the first bad byte.
- **Validating.** `validate_config(raw, types)` takes the installed extension classes by `type_name`
  (`infrastructure.discovery.discover_extensions` finds them, refusing the unsupported and the
  malformed ones) and returns a `Config`: `extensions` (`ExtensionSetup`: type and validated
  config) and `endpoints` per instance in config order, the `Topology` (sites are named
  `<group>.<site>`, D34), `fingerprint_dedup` per endpoint (no longer than the retention),
  `admin_endpoint`, `new_accounts`, `routing`, `retention` (at most 3650 days) and `peers`.
  Otherwise it raises a `ConfigError` listing the problems with their places and without values.
  Its last step builds each instance and calls `set_endpoints` on a fresh one with an inert hub, so
  the same call serves `check-config` and every reload.
- **Reloading.** `watch_files(paths, on_change, stop)` (over `watchfiles`) calls
  `HubRuntime.refresh()` when `chatko.yaml` or the routing script changes; the runtime applies a
  `LOADED` outcome (§5.5).
- **`chatko check-config`** (`app.check_config`): validate, load the routing script with
  `load_script`, run the files `test_*.py` next to the config with `pytest` in a subprocess (optional extra
  `chatko[test]`; skipped with a message if missing). Exit 0 if all pass, 1 otherwise.

### 5.5 The running hub

`HubRuntime` (`application.runtime`, D42) is the hub as one object over its ports, `HubPorts`:
clock, ids, the message, outbox and history repositories, the account registry, the config loader
and the routing script source. It builds the services of the table above, and the tests run it
over the in-memory fakes. `HubSettings` holds the outbox's settings and the upkeep periods.

- **`start`.** Load the config, or raise `StartError` with its problems; restore the history;
  construct each instance with its `ExtensionHub` and give it its endpoints; load the routing
  script (D39); start the instances one by one; start the outbox worker, which loads the pending
  deliveries, and make what waits for a started instance due at once. An instance whose `start`
  raises stays constructed but not running: its deliveries wait (`Retry`) and the admin is told
  (key `extension:<instance>`). Then the upkeep tasks: save the history every 10 s, prune old
  messages and arrivals at start and daily.
- **`refresh`** (the watcher's callback; one at a time). Reload the config. If a new one loaded:
  take out of service each instance that is gone, whose `ExtensionSetup` changed or that is not
  running (its deliveries in progress end, then `stop`); reload the routing script; give each
  remaining instance whose endpoints changed the new ordered set; construct the new instances;
  publish the new `Installation`, with the new admin endpoint; start the new instances. Until the
  new snapshot is published, admin notices go to the admin endpoint of the old one (D43). The script's swap and the new
  snapshot happen in one step of the event loop, so no message is routed by a new script against
  the old topology or the other way round. Without a new config, only the script is reloaded.
- **`stop`.** Stop the upkeep, the worker (its grace), each instance, wait for the submissions in
  progress (`InboundPipeline.drain`), save the history.
- `config` is the latest valid config: the routing script it names is the one to load.

`app.run` is the composition root: `run_hub(config, env, types, data=…, stop=…)` opens the
SQLite database `chatko.sqlite3` in the data directory, builds the `HubRuntime` over the real
adapters (`SystemClock`, `RandomIds`, `SqliteStore`, `SqliteHistory`, `SqliteAccounts`,
`FileConfigLoader`, and `ConfiguredScriptSource`, which reads the script the current config
names, relative to the config file), starts it and watches the config file and the routing
script until `stop` is set; when the config names another script the watch moves to it. It
returns 1 when the hub cannot start and 0 after a stop. `serve` sets `stop` on SIGINT and
SIGTERM (where the event loop cannot, on Windows, Ctrl+C cancels the run, which still stops in
order). `chatko run [--config config/chatko.yaml] [--env-file .env] [--data data] [--log-level
INFO]` logs to stderr and runs the installed extensions (`discover_extensions`).

## 6. Technology

| Area | Choice |
|---|---|
| Language | Python 3.12+, asyncio, full type hints |
| Tooling | `uv` (environments, lock file), `ruff` (lint + format), `mypy --strict`, `import-linter`, `pre-commit` |
| Tests | `pytest`, `pytest-asyncio`, `pytest-cov`/`coverage` (branch), `hypothesis` (property tests); `respx`/local test servers for HTTP fakes |
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
- only tests import the test kits (`extension_api.testing`, `routing_api.testing`) and the
  application's fakes (`application.testing`): no module of the code base does, except the kits
  themselves (a `protected` contract, which unlike a `forbidden` one also covers a package
  importing its own kit);
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
- Rules that must hold for any input (the routing invariants) are also `hypothesis` property tests.
- Time-dependent code takes the `Clock` port, and its tests move a `FakeClock` instead of sleeping.
