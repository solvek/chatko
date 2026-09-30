# Roadmap by session

How to build chatko in a series of AI-assisted working sessions. A **session** is one conversation with
a coding agent (Claude Code) that starts with an empty context, has one goal, and ends with a result that
can be checked and committed. The phases are those of [design.md §12](design.md#12-plan).

## How to run a session

Start every session with this prompt, filling in the session id:

```
Continue chatko with session <Sxx> from docs/roadmap.md.
Read AGENTS.md, the roadmap and the docs the session needs. Stop when its "Done when" holds.
Keep design.md, architecture.md and decisions.md in sync. Mark the session done in the roadmap.
Do not commit without my permission.
```

Rules:
- One session, one goal. If a session grows past its goal, stop, record what is left in this file as a
  new session, and start it fresh. A clean context is cheaper and better than a long one.
- A session ends green: tests, `ruff`, `mypy --strict` and `lint-imports` pass (once they exist, S08).
- Each session updates the **Status** column below and records surprises in the right document.
- Sessions marked *with owner* need the owner's hands (phones, bot tokens, radios). The agent guides
  step by step and asks for the result after each step.

## Choosing the model and effort

| Model | Price, input / output per 1M tokens | Use for |
|---|---|---|
| **Fable 5.1** | $10 / $50 | Decisions that are expensive to change later: the extension API, the final review before release. Rarely. |
| **Opus 5.5** | $4 / $20 | The default for design and non-trivial code: concurrency, protocols, unfamiliar code bases, debugging. |
| **Sonnet 5.5** | $2 / $10 | Well-specified implementation, tooling, docs, guided manual tests. |
| **Haiku 4.5** | $1 / $5 | Trivial chores only: typo fixes, reformatting, renaming. It has no effort levels. |

Effort (`low` → `medium` → `high` → `xhigh` → `max`) sets how much the model thinks and checks:
- `medium`: conversational sessions and guided tests, where the owner's hands are the bottleneck.
- `high`: normal implementation sessions.
- `xhigh`: hard code (threads ↔ asyncio, retry state machines, a foreign Kotlin code base) and reviews.
- `max`: only to get unstuck on a problem that `xhigh` did not solve.

Escalate instead of looping: if a session fails at the same problem twice, restart it one step up
(Sonnet → Opus, or effort +1).

## Sessions

| # | Phase | Goal | Model | Effort | Status |
|---|---|---|---|---|---|
| S01 | 0 | Spike S1: Briar relay on 3 phones (*with owner*) | Opus 5.5 | medium | done |
| S02 | 0 | Spike S2a: local Meshtastic lab, channel messages | Opus 5.5 | high | done |
| S03 | 0 | Spike S2b: PKI direct messages, keys, ACKs, persistence, provisioning | Opus 5.5 | xhigh | done |
| S04 | 0 | Spike S2c: Kyiv broker, read-only; questions for the Kyiv community | Sonnet 5.5 | medium | todo |
| S05 | 0 | Spike S3a: build and run briar-headless, contacts API with a phone | Opus 5.5 | high | todo |
| S06 | 0 | Spike S3b: private-group internals of Briar, read/post/join patch plan | Opus 5.5 | xhigh | todo |
| S07 | 0 | Phase 0 wrap-up: all (verify) answered, design and roadmap revised | Opus 5.5 | high | todo |
| S08 | 1 | Project skeleton, tooling and CI | Sonnet 5.5 | high | todo |
| S09 | 1 | Domain model and label generator | Opus 5.5 | high | todo |
| S10 | 1 | Extension API, routing API and contract test suite design | Fable 5.1 | xhigh | todo |
| S11 | 1 | Inbound pipeline, routing invariants and outbox worker | Opus 5.5 | high | todo |
| S12 | 1 | Routing engine: script loading, defaults, `label` hook, test kit, example script | Opus 5.5 | high | todo |
| S13 | 1 | SQLite repositories and migrations | Sonnet 5.5 | high | todo |
| S14 | 1 | Configuration: models, `${ENV}`, people, admin notices, hot reload, `check-config` | Sonnet 5.5 | high | todo |
| S15 | 1 | Extension discovery, composition root, `chatko run`, fake extension end to end | Opus 5.5 | high | todo |
| S16 | 1 | Phase 1 review | Opus 5.5 | xhigh | todo |
| S17 | 2 | Telegram: port, group and private-chat endpoints, allowed chats | Opus 5.5 | high | todo |
| S18 | 2 | Telegram live test with two groups (*with owner*) | Sonnet 5.5 | medium | todo |
| S19 | 3 | Meshtastic: asyncio adapter over the `meshtastic` library (TCP to `meshtasticd`), provisioning from the config | Opus 5.5 | xhigh | todo |
| S20 | 3 | Meshtastic: `channel` endpoints, splitting, rate limit, de-duplication, `LongFast` source | Opus 5.5 | high | todo |
| S21 | 3 | Meshtastic: `dm` endpoints, ACKs and retries | Opus 5.5 | xhigh | todo |
| S22 | 3 | Meshtastic lab integration tests, radio ⇄ Telegram | Opus 5.5 | high | todo |
| S23 | 3 | Field test on the Kyiv mesh (*with owner*, needs a gateway or hardware) | Sonnet 5.5 | medium | todo |
| S24 | 4 | briar-headless fork: private-group API (Kotlin) | Opus 5.5 | xhigh | todo |
| S25 | 4 | briar-headless Docker image; upstream merge request | Sonnet 5.5 | high | todo |
| S26 | 4 | Briar extension: port, endpoints, posts in and out, own-post filter | Opus 5.5 | high | todo |
| S27 | 4 | `briarctl`: contacts, groups, invitations (command-line tool) | Sonnet 5.5 | high | todo |
| S28 | 4 | Three-network test in the cloud setup: Briar ⇄ Telegram ⇄ Meshtastic (`channel` and `dm`) (*with owner*) | Sonnet 5.5 | medium | todo |
| S29 | 5 | Production deployment, backups, operations docs | Sonnet 5.5 | high | todo |
| S30 | 5 | Release review: security, code, docs | Fable 5.1 | high | todo |

Rough cost profile: about two thirds of the sessions on Opus 5.5, a third on Sonnet 5.5, two on
Fable 5.1.

## Session details

### Phase 0: spikes

**S01. Briar relay (spike S1).** *With owner*, phones H, B, A. Read spikes.md S1 and design.md §7.
Guide the owner through the steps one at a time: what to do on which phone, what to check, what to note;
ask for the result after each step.
Done when: the "Result" of S1 is filled in (date, Briar version, evidence), the answered (verify) marks
in design.md §7.2–7.3 are resolved, and a decision is recorded in decisions.md if the result changes the
design.

**S02. Local Meshtastic lab, part 1.** `docker compose` with Mosquitto and two `meshtasticd` containers
(hub, radio) under `lab/`. Done when: the image and config for a simulated radio are known (amd64 and
arm64); a private-channel message goes radio → hub and hub → radio through MQTT and is seen via
`TCPInterface`; the packet fields are recorded in spikes.md.

**S03. Local Meshtastic lab, part 2.** PKI direct messages both ways; `pki_encrypted` and the sender's
public key in the Python API; how keys are learned (NodeInfo) and how long it takes; ACKs over MQTT;
`(from, id)` stability; node identity across restarts. Provisioning from code is already answered in
S02 (D25); check whether waiting for each admin response replaces the 1 s pause. Build on `lab/` and
read the firmware source where behaviour is unclear. Done when: all S2 checkboxes except the Kyiv and
hardware ones are answered. Result: spikes.md S2 part 2, D26.

**S04. Kyiv broker.** A read-only client receives `LongFast` text. Draft (in Ukrainian, for the owner to
send) the questions to the Kyiv community: PKI topic policy, downlink, gateway firmware, whether
relays and gateways run with "Ignore MQTT" on, whether gateways downlink `LongFast` (a gateway learns
the hub's node only that way, and downlinks a direct message only if it knows both nodes), and how
many radios have "OK to MQTT" on (design.md §6.2, spike S2 part 2). Done when:
receiving works, the questions are sent, and design.md §6.2 notes what is still waiting for an answer.

**S05. briar-headless, part 1.** Build `x86LinuxJar` (and `aarch64LinuxJar`) from upstream, note the JDK;
run it in Docker with a data volume and non-interactive account creation; exchange links with a phone;
private messages both ways over the WebSocket. Done when: the S3 checkboxes 1–3 are answered.

**S06. briar-headless, part 2.** Read `PrivateGroupManager`, `GroupInvitationManager` and how the Android
app uses them. Write the patch plan of design.md §7.4 (D24): create, list, members, invite, dissolve,
invitations from others, read and post messages, events; tests in upstream style; the upstream
contribution rules. Check whether `briar-headless` has the LAN transport (for a future home hub, D17).
Done when: the S3 checkboxes 4–5 are answered and design.md §7.4 matches the plan.

**S07. Phase 0 wrap-up.** Walk through every (verify) in design.md. Record decisions that the spikes
changed. Re-plan the sessions below if needed. Done when: design.md has no (verify) that affects v1,
and phase 1 can start.

### Phase 1: core

**S08. Skeleton.** `pyproject.toml` with `uv`; packages as in architecture.md §2; `ruff`, `mypy
--strict`, `import-linter` contracts (the dependency rule, including `routing_api`), `pytest` with branch
coverage and the gates from architecture.md §7; `pre-commit`; GitHub Actions on Linux, macOS and Windows.
Done when: CI passes on an empty project and a deliberate forbidden import fails `lint-imports`.

**S09. Domain.** `EndpointRef`, `Group`, `Account`, `Person`, `Message`, `Target`, `Delivery`, the
fingerprint, and the label generator (Ukrainian KMU-2010 and Russian transliteration, fallbacks). No I/O.
Done when: domain coverage ≥ 95 % and the label examples of design.md §8 are tests.

**S10. Extension and routing APIs.** The most expensive decision to change later, hence Fable. Settle
`Extension`, `EndpointProvider`, `HubContext`, the message and delivery types, and API versioning (room
for more protocols later, D22). Settle the routing API (architecture.md §4): `RoutedMessage`,
`RoutingContext`, targets, `mirror`, `label`/`default_label`, the fingerprint, and room for peer hubs
(D17). Write the contract test suite skeleton in `extension_api.testing`. Done when: architecture.md §3
and §4 describe the real APIs, a decision entry records them, and the contract suite runs against a stub.

**S11. Pipeline.** Ports (repositories, clock, ids), in-memory fakes next to them, `InboundPipeline`
(own posts, transport-id dedup, person lookup, fingerprint, persistence, labels, one outbox row per
target), `RoutingInvariants` (design.md §9.3) with property-style tests, `OutboxWorker` (due rows,
`deliver`, backoff, restart safety). The router is a stub that calls `mirror`. Done when: design.md §9.1
and §9.3 are tests.

**S12. Routing engine.** `RoutingEngine`: load `routing.py` from the config directory, validate it,
hot-reload it keeping the last good version, fall back to the defaults on errors and post an admin
notice; the optional `label` hook and `default_label` (design.md §8); `routing_api.testing` (fake
installation, assertions); `routing.example.py` tested in CI. Done when: design.md §9.2, §9.4 and §9.5
are tests, and the example script passes its own tests.

**S13. Storage.** `aiosqlite` repositories and schema migrations for runtime state (messages, outbox,
de-duplication, last heard, accounts seen). The same repository tests run against the fakes and SQLite.
Done when: all repository ports have a SQLite implementation passing the shared tests.

**S14. Configuration.** Pydantic models for the core (groups, legs, sources, people, `admin_notices`,
`routing`, room for `peers`) and hooks for each extension's models; YAML safe load; `${ENV}`
substitution; reload with `watchfiles`, keeping the last valid config and reporting errors as admin
notices; `AdminNotifier`; `chatko check-config` (also loads and tests the routing script). Done when:
`config.example.yaml` validates with fake extensions, and invalid configs give clear errors.

**S15. Wiring.** Entry-point discovery (`chatko.extensions`), the composition root, `chatko run`,
`FakeExtension`. Done when: an end-to-end test runs the hub with two fake extensions and a message crosses
from one leg to the other through SQLite.

**S16. Phase 1 review.** `/code-review` at high effort, coverage and architecture check, docs in sync
with the code. Done when: findings are fixed or recorded as sessions.

### Phase 2: Telegram

**S17. Telegram endpoints.** `TelegramApi` port over `aiogram` 3 and its fake; group and private-chat
endpoints in and out; the author account and display name; drop the bot's own posts; serve only
configured chats, leave others and post an admin notice with the chat id (design.md §5). Done when: the
extension passes the contract suite and its coverage is ≥ 85 %.

**S18. Live Telegram test.** *With owner*: a bot from BotFather, two test groups, the hub running
locally. Done when: two groups work independently, foreign groups are left, labels look right.

### Phase 3: Meshtastic

**S19. Adapter.** A `MeshApi` port and its fake; an adapter that bridges the thread-based
`TCPInterface` to asyncio (serial and BLE later, D20, without changing the port); provisioning of the
hub's node from the config (names, region, `ignore_mqtt`, private key, channels, PSKs, MQTT,
contacts; waiting for each admin response; D25, D26), reconnection after node reboots, ACKs and NAKs
matched by request id. Done when: the adapter is tested against the lab from S02 and S03.

**S20. Channel endpoints.** `channel` endpoints, splitting into ≤ 200-byte parts with at most 3 parts and
truncation, placeholders for non-text, per-node send interval, de-duplication across gateways, drop own
packets, authors from NodeInfo, `LongFast` as a source, last-heard tracking. Done when: design.md §6.3
and §6.4 are covered by tests.

**S21. DM endpoints.** `dm` endpoints with node lists: out to every listed node, in from listed nodes
(first endpoint wins), the delivery states of D26 (the node's ACK, not the implicit one; retry when
the node is heard again or after a key exchange), listed nodes kept as favorites, key-mismatch admin
notices, at least 2 s between texts. Done when: design.md §6.2 is
covered by tests.

**S22. Lab integration.** Opt-in integration tests with the docker lab (Mosquitto, two `meshtasticd`),
run nightly in CI. Done when: radio ⇄ Telegram works in the lab with both kinds of endpoint.

**S23. Kyiv field test.** *With owner*, once a gateway or a hardware node is available. Done when:
a message reaches a real radio through a `dm` endpoint (and a `channel` endpoint, if a gateway knows the
channel), and the results are in spikes.md.

### Phase 4: Briar

**S24. Private-group API.** The patch from S06 in our fork of briar-headless (design.md §7.4), with
tests in upstream style. Done when: every endpoint of design.md §7.4 works against a phone.

**S25. Packaging.** Docker image for the patched briar-headless (amd64, arm64), non-interactive account
setup, the compose service; prepare the upstream merge request. Done when: the image runs in the lab and
the merge request is ready for the owner to submit.

**S26. Briar endpoints.** `BriarApi` port over `httpx` and `websockets` and its fake; the Briar groups
in `chatko.yaml` as endpoints; posts in and out with author accounts; drop its own posts. No contacts,
groups or invitations (D24). Done when: design.md §7.1 (the extension's part) and §7.2 are covered by
tests, the extension passes the contract suite, coverage ≥ 85 %.

**S27. briarctl.** The command-line tool of design.md §7.5 as the separate `briarctl` package: its own
small REST client and fake, plain and `--json` output, an `import-linter` contract that keeps it apart
from chatko, README steps for making a group with the hub as the creator. Done when: every command is
tested against the fake and tried once against the lab `briar-headless` with a phone.

**S28. Three networks.** *With owner*: the cloud setup of D20 (a Briar group made with `briarctl`, a Telegram
group, Meshtastic in the lab or on the Kyiv mesh). Done when: one group works across all three networks,
with Meshtastic both as a channel and as DMs to several nodes.

### Phase 5: release

**S29. Deployment.** Production compose file, daily backup of `config/` and `data/`, a guide for a Linux
VM (Oracle Cloud Always Free as the example), operations section in the README. Done when: the hub runs
on a server and a restore from backup is tested.

**S30. Release review.** `/security-review`, full code review, docs versus code, the known limitations in
the README. Done when: findings are fixed or accepted, and v1 is tagged by the owner.

### Later (not in v1)

Planned as sessions once v1 is released (D17, D20, D22):
- a physical hub node: serial, BLE and TCP connections in the Meshtastic adapter, tested with hardware;
- several hubs: `peers` in the config, peer-relayed authors, fingerprint de-duplication on shared
  endpoints; a home hub on a Raspberry Pi (Briar on the local Wi-Fi, a physical node);
- a Signal extension over signal-cli-rest-api (D19);
- a web UI, and commands if they turn out to be needed.
