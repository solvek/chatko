# Roadmap by session

How to build chatko in a series of AI-assisted working sessions. A **session** is one conversation with
a coding agent (Claude Code) that starts with an empty context, has one goal, and ends with a result that
can be checked and committed. The phases are those of [design.md §13](design.md#13-plan).

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
| S02 | 0 | Spike S2a: local Meshtastic lab, channel messages | Opus 5.5 | high | todo |
| S03 | 0 | Spike S2b: PKI direct messages, keys, ACKs, persistence, provisioning | Opus 5.5 | xhigh | todo |
| S04 | 0 | Spike S2c: Kyiv broker, read-only; questions for the Kyiv community | Sonnet 5.5 | medium | todo |
| S05 | 0 | Spike S3a: build and run briar-headless, contacts API with a phone | Opus 5.5 | high | todo |
| S06 | 0 | Spike S3b: private-group internals of Briar, patch plan | Opus 5.5 | xhigh | todo |
| S07 | 0 | Phase 0 wrap-up: all (verify) answered, design and roadmap revised | Opus 5.5 | high | todo |
| S08 | 1 | Project skeleton, tooling and CI | Sonnet 5.5 | high | todo |
| S09 | 1 | Domain model and nick generator | Opus 5.5 | high | todo |
| S10 | 1 | Extension API and contract test suite design | Fable 5.1 | xhigh | todo |
| S11 | 1 | Inbound router and outbox worker | Opus 5.5 | high | todo |
| S12 | 1 | Membership, identity and nick services, event bus | Opus 5.5 | high | todo |
| S13 | 1 | Command service and feed service | Sonnet 5.5 | high | todo |
| S14 | 1 | SQLite repositories and migrations | Sonnet 5.5 | high | todo |
| S15 | 1 | Configuration: models, `${ENV}`, hot reload, `check-config` | Sonnet 5.5 | high | todo |
| S16 | 1 | Extension discovery, composition root, `chatko run`, fake extension end to end | Opus 5.5 | high | todo |
| S17 | 1 | Phase 1 review | Opus 5.5 | xhigh | todo |
| S18 | 2 | Telegram: port, legs, allowed chats | Opus 5.5 | high | todo |
| S19 | 2 | Telegram: membership source and control surface | Opus 5.5 | high | todo |
| S20 | 2 | Telegram live test with two groups (*with owner*) | Sonnet 5.5 | medium | todo |
| S21 | 3 | Meshtastic: asyncio adapter over the `meshtastic` library, node provisioning | Opus 5.5 | xhigh | todo |
| S22 | 3 | Meshtastic: `channel` legs, splitting, rate limit, de-duplication | Opus 5.5 | high | todo |
| S23 | 3 | Meshtastic: `dm` legs, ACKs and retries, choice between modes | Opus 5.5 | xhigh | todo |
| S24 | 3 | Meshtastic: `/mesh` linking, channel QR codes, default group, feeds | Sonnet 5.5 | high | todo |
| S25 | 3 | Meshtastic lab integration tests, radio ⇄ Telegram | Opus 5.5 | high | todo |
| S26 | 3 | Field test on the Kyiv mesh (*with owner*, needs a gateway or hardware) | Sonnet 5.5 | medium | todo |
| S27 | 4 | briar-headless fork: private-group API (Kotlin) | Opus 5.5 | xhigh | todo |
| S28 | 4 | briar-headless Docker image; upstream merge request | Sonnet 5.5 | high | todo |
| S29 | 4 | Briar extension: port, legs, own-post filter | Opus 5.5 | high | todo |
| S30 | 4 | Briar extension: `/briar` linking, invitations, replace and remove | Opus 5.5 | high | todo |
| S31 | 4 | Three-network test: Briar ⇄ Telegram ⇄ Meshtastic (*with owner*) | Sonnet 5.5 | medium | todo |
| S32 | 5 | Production deployment, backups, operations docs | Sonnet 5.5 | high | todo |
| S33 | 5 | Release review: security, code, docs | Fable 5.1 | high | todo |

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
`(from, id)` stability; node identity across restarts; setting channels, PSKs and MQTT from code.
Read the firmware source where behaviour is unclear. Done when: all S2 checkboxes except the Kyiv and
hardware ones are answered.

**S04. Kyiv broker.** A read-only client receives `LongFast` text. Draft (in Ukrainian, for the owner to
send) the questions to the Kyiv community: PKI topic policy, downlink, gateway firmware. Done when:
receiving works, the questions are sent, and design.md §6.2 notes what is still waiting for an answer.

**S05. briar-headless, part 1.** Build `x86LinuxJar` (and `aarch64LinuxJar`) from upstream, note the JDK;
run it in Docker with a data volume and non-interactive account creation; exchange links with a phone;
private messages both ways over the WebSocket. Done when: the S3 checkboxes 1–3 are answered.

**S06. briar-headless, part 2.** Read `PrivateGroupManager`, `GroupInvitationManager` and how the Android
app uses them. Write the patch plan: methods, endpoints, events, tests in upstream style; whether a member
can be removed; the upstream contribution rules. Done when: the S3 checkboxes 4–5 are answered and
design.md §7.4 matches the plan.

**S07. Phase 0 wrap-up.** Walk through every (verify) in design.md. Record decisions that the spikes
changed. Re-plan the sessions below if needed. Done when: design.md has no (verify) that affects v1,
and phase 1 can start.

### Phase 1: core

**S08. Skeleton.** `pyproject.toml` with `uv`; packages as in architecture.md §2; `ruff`, `mypy
--strict`, `import-linter` contracts (the dependency rule), `pytest` with branch coverage and the gates
from architecture.md §6; `pre-commit`; GitHub Actions on Linux, macOS and Windows. Done when: CI passes on
an empty project and a deliberate forbidden import fails `lint-imports`.

**S09. Domain.** `Member`, `Identity`, `IdentityKey`, `Nick` with validation and generator
(Ukrainian KMU-2010 and Russian transliteration, collisions), `Group`, `LegRef`, `Message`, `Delivery`.
No I/O. Done when: domain coverage ≥ 95 % and the nick examples of design.md §8 are tests.

**S10. Extension API.** The most expensive decision to change later, hence Fable. Settle `Extension`,
the capability protocols, `HubContext`, the message, command, reply and delivery types, events that
extensions can subscribe to, and API versioning. Solve the Telegram membership gap here: the Bot API
cannot list the members of a group, so a membership source may only know members it has seen
(`chat_member` updates, messages) and check one identity at a time (`getChatMember`); the
`MembershipSource` protocol must allow that. Write the contract test suite skeleton in
`extension_api.testing`. Done when: architecture.md §3 describes the real API, a decision entry records
it, and the contract suite runs against a stub.

**S11. Routing.** Ports (repositories, clock, ids), in-memory fakes next to them, `InboundRouter`
(dedup, author, group, persistence, one outbox row per other leg), `OutboxWorker` (due rows, `deliver`,
backoff, restart safety). Done when: the rules of design.md §10 are tests.

**S12. Members.** `MembershipService`, `IdentityService` (uniqueness: one node per member, at most one
Briar identity), `NickService`, the in-process event bus. Done when: membership sync, linking and nick
changes are tested, including the events they emit.

**S13. Commands and feeds.** `CommandService` (parsing, authorization, core commands `/start`, `/help`,
`/me`, `/nick`, extension commands), `FeedService`. Done when: design.md §5.2 core commands and §6.5
behaviour are tests.

**S14. Storage.** `aiosqlite` repositories and schema migrations. The same repository tests run against
the fakes and SQLite. Done when: all repository ports have a SQLite implementation passing the shared
tests.

**S15. Configuration.** Pydantic models for the core and hooks for each extension's models; YAML safe
load; `${ENV}` substitution; reload with `watchfiles`, keeping the last valid config and reporting errors
to admins; `chatko check-config`. Done when: `config.example.yaml` validates with fake extensions, and
invalid configs give clear errors.

**S16. Wiring.** Entry-point discovery (`chatko.extensions`), the composition root, `chatko run`,
`FakeExtension`. Done when: an end-to-end test runs the hub with two fake extensions and a message crosses
from one leg to the other through SQLite.

**S17. Phase 1 review.** `/code-review` at high effort, coverage and architecture check, docs in sync
with the code. Done when: findings are fixed or recorded as sessions.

### Phase 2: Telegram

**S18. Telegram legs.** `TelegramApi` port over `aiogram` 3 and its fake; group legs in and out; drop
the bot's own posts; serve only configured chats, leave others, tell admins (design.md §5.1). Done when:
the extension passes the contract suite and its coverage is ≥ 85 %.

**S19. Membership and control surface.** `chat_member` handling, the membership source as settled in
S10, the control surface (commands as buttons, images for QR codes), Ukrainian texts. Done when:
design.md §5 is covered by tests.

**S20. Live Telegram test.** *With owner*: a bot from BotFather, two test groups, the hub running
locally. Done when: two groups work independently, foreign groups are left, nicks and `/me` work.

### Phase 3: Meshtastic

**S21. Adapter.** A `MeshApi` port and its fake; an adapter that bridges the thread-based
`TCPInterface` (and serial/BLE) to asyncio; provisioning of the hub's node from the config (names,
channels, PSKs, MQTT). Done when: the adapter is tested against the lab from S02.

**S22. Channel legs.** `channel` delivery, splitting into ≤ 200-byte parts with at most 3 parts and
truncation, placeholders for non-text, per-node send interval, de-duplication across gateways, drop own
packets. Done when: design.md §6.4 is covered by tests.

**S23. DM legs.** `dm` delivery to every linked node, ACK handling and retry when the node is heard
again, the choice between `channel` and `dm` per member. Done when: design.md §6.2 is covered by tests.

**S24. Member features.** `/mesh` linking by code, unlinking, channel URLs and QR codes, `~` for unlinked
nodes, default group and `#group` prefix, feeds. Done when: design.md §6.3 and §6.5 are covered by tests.

**S25. Lab integration.** Opt-in integration tests with the docker lab (Mosquitto, two `meshtasticd`),
run nightly in CI. Done when: radio ⇄ Telegram works in the lab in both modes.

**S26. Kyiv field test.** *With owner*, once a gateway or a hardware node is available. Done when:
a message reaches a real radio in `dm` mode (and in `channel` mode, if a gateway knows the channel), and
the results are in spikes.md.

### Phase 4: Briar

**S27. Private-group API.** The patch from S06 in our fork of briar-headless, with tests in upstream
style. Done when: every endpoint of design.md §7.4 works against a phone.

**S28. Packaging.** Docker image for the patched briar-headless (amd64, arm64), non-interactive account
setup, the compose service; prepare the upstream merge request. Done when: the image runs in the lab and
the merge request is ready for the owner to submit.

**S29. Briar legs.** `BriarApi` port over `httpx` and `websockets` and its fake; the hub creates each
group's Briar private group; posts in and out; drop its own posts. Done when: the extension passes the
contract suite, coverage ≥ 85 %.

**S30. Briar linking.** `/briar` flow (design.md §7.2): pending contact, `ContactAddedEvent`, identity,
invitations on joining a group, replace and remove, unlinked authors. Done when: §7.2 is covered by tests.

**S31. Three networks.** *With owner*: phones, Telegram, the Meshtastic lab or the Kyiv mesh. Done when:
one group works across all three networks.

### Phase 5: release

**S32. Deployment.** Production compose file, daily backup of `config/` and `data/`, a guide for a Linux
VM (Oracle Cloud Always Free as the example), operations section in the README. Done when: the hub runs
on a server and a restore from backup is tested.

**S33. Release review.** `/security-review`, full code review, docs versus code, the known limitations in
the README. Done when: findings are fixed or accepted, and v1 is tagged by the owner.
