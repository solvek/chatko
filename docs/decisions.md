# Architectural decisions

A short, append-only log. Each entry: context → decision → consequences.

## D1. Group messages only in v1
Direct messages through Briar cannot be relayed by other members, and every network has a native group
mechanism (a Telegram group, a Briar private group, a Meshtastic channel).
**Decision:** v1 mirrors groups only. There are no direct messages.
**Consequences:** simple routing and UX ("just write"). There is no privacy between members of a
group. The data model keeps room for direct messages later.

## D2. Own hub instead of building on mesh-api
mr-tbot/mesh-api needs a physical radio and has no virtual node. Its Telegram extension serves one chat,
and it is a ~10k-line monolith focused on AI and alerts.
**Decision:** a small Python hub of our own. We borrow ideas from mesh-api (message splitting,
MeshCore later).
**Consequences:** less code to carry, and routing fits our model. We maintain the hub ourselves.

## D3. The virtual Meshtastic node is `meshtasticd`, not our own implementation
**Decision:** run the official `meshtasticd` with a simulated radio and its MQTT client. The hub talks
to it over the TCP API with the `meshtastic` Python library. One container per extension instance (per
MQTT broker).
**Consequences:** PKI, channel crypto and NodeInfo come for free and follow firmware updates. Each node
is limited to 8 channels and one broker.

## D4. Membership comes from a pluggable membership source; v1 uses Telegram groups
**Decision:** a group names its membership source. In v1 it is a Telegram group, so whoever is in the
Telegram group is a member. The bot serves only chats listed in the config and leaves any other group.
**Consequences:** admins manage people with ordinary Telegram tools. Removing someone from the Briar
or Meshtastic sites needs a new Briar group or a new channel PSK. Another source can replace Telegram
later without core changes (see D9).

## D5. Admin config is read-only for the hub; user state lives in SQLite
**Decision:** `chatko.yaml` (extensions, groups, feeds, admins) is written only by the admin. Nicks,
identities and preferences are stored in SQLite.
**Consequences:** the admin's file is never rewritten by the program. Backups must include `data/`.

## D6. Briar private groups through a briar-headless patch
briar-headless has no private-group API.
**Decision:** add private-group endpoints to briar-headless in our fork and offer them upstream.
**Consequences:** we build briar-headless ourselves.

## D7. Secrets only in environment variables
**Decision:** `${VAR}` substitution in the YAML, values in `.env`. `.env`, `config/` and `data/` are
git-ignored.
**Consequences:** the repository is safe to publish. Example files show the format.

## D8. English everywhere in the repository
**Decision:** code, comments, docs and commit messages are in English. User-facing bot texts get
localization, with Ukrainian as the first translation.

## D9. Every network is an extension; Telegram is not special
**Decision:** the core knows no network. Extensions implement capabilities (sites, membership source,
control surface, commands, feeds) through the public `chatko.extension_api`. They are discovered through
entry points, including the built-in ones. Members have an internal id, and all network accounts are
identities attached to it.
**Consequences:** networks can be added or removed without core changes. Authentication can move from
Telegram to another method (e.g. web) by adding an extension. A small, stable extension API must be
designed and versioned carefully.

## D10. Clean architecture with enforced layers and quality gates
The owner requires high code quality, clean architecture and good test coverage.
**Decision:** layers `domain` ← `application` ← `extension_api` ← extensions, enforced with
`import-linter`. `mypy --strict`, `ruff`, and coverage gates (≥ 95 % for domain and application, ≥ 85 %
per extension) apply to every PR. A shared contract test suite runs against every extension.
**Consequences:** slightly slower start and more structure than a small script. In return, extensions are
safe to add, and the routing core can be changed with confidence.

## D11. License: GPL-3.0-or-later
We use the GPL-3.0 `meshtastic` Python library and patch GPL-3.0 Briar code.
**Decision:** chatko is GPL-3.0-or-later.
**Consequences:** anyone who distributes chatko (e.g. a Docker image) must provide the source. Running
it as a service imposes no obligations (GPL, not AGPL).

## D12. Pure Python hub, Docker for the rest; develop locally first
**Decision:** the hub runs anywhere Python 3.12+ runs. `meshtasticd` and `briar-headless` run in
Docker. Development happens on the owner's computer with a local MQTT broker and a second virtual node
instead of hardware. The production host is any Linux server (x86-64 or ARM64).
**Consequences:** no dependency on a particular cloud. Hardware gateways and the Kyiv broker are tested
once the local lab works.

## D13. The hub's Meshtastic node can be virtual or physical
mr-tbot/mesh-api drives a physical node over USB/TCP/BLE. The `meshtastic` Python library has the same
API for all connection types, including TCP to `meshtasticd`.
**Decision:** each `meshtastic` extension instance connects to one node through a configurable
connection: TCP to a `meshtasticd` container (virtual node), or USB serial, BLE or TCP to a real
radio (physical node). This extends D3.
**Consequences:** a hub near the mesh can use a radio directly and needs no gateway or MQTT. A hub in
the cloud uses a virtual node. The extension code is the same for both.

## D14. Two Meshtastic delivery modes, `dm` by default
A group usually has only one or two radio nodes, and an always-online gateway that knows a private
channel is not guaranteed.
**Decision:** both `dm` (PKI direct messages to each member's linked nodes) and `channel` (a broadcast on
a private channel) are first-class. `dm` is the default.
**Consequences:** `dm` works through any gateway that forwards PKI direct messages, but costs one packet
per recipient node. `channel` is cheaper on air but needs a gateway that knows the channel.

## D15. Briar members are cut off by re-creating the group
Spike S1 (Briar 1.5.20) showed that a private group has no way to remove a member, and that deleting the
contact does not cut a member off: they keep reading and posting through other members. Dissolving the
group does stop it for everyone. Relay through other members works, and a "Reveal contacts" by one of
two members is enough.
**Decision:** `/briar remove` deletes the contact and the identity and does not try to remove the member
from groups. Posts from such an account are relayed as unlinked or ignored, as configured. A real
cut-off is done by re-creating the group's Briar site (dissolve, create, invite the linked members). The
`/briar` help tells members to add each other nearby and to reveal their contacts in the group.
**Consequences:** the briar-headless patch needs no remove-member endpoint; `DELETE /v1/groups/{groupId}`
(dissolve) is required. Re-creating a Briar site loses its history on members' phones and needs every
member to reach the hub directly once to accept the new invitation.

## D16. Routing is a Python script; the YAML declares what exists
Fixed rules ("mirror all sites of a group", feeds as a separate feature) are not flexible enough: the
owner wants to route between any endpoints (several Briar groups, a broadcast to one member), and later
between several hubs.
**Decision:** `chatko.yaml` stays declarative (extensions, groups, sites, sources, admins, peers).
Routing is `config/routing.py`: a pure function `route(message, ctx) → targets` that imports only the
public `chatko.routing_api`. The core ships a default router (`mirror`: all other sites of the group)
used when there is no script, and as the fallback when the script raises. The core always enforces the
invariants against echoes and duplicates (own posts, source endpoint, at most once per target,
transport-id and optional fingerprint de-duplication). Feeds become routes from a source to members.
**Consequences:** a second public, versioned API (`routing_api`) next to `extension_api`, with its own
test kit for admins. The script is trusted code, like the config. `FeedSource` and the `feeds:` section
disappear; sources are endpoints outside groups. The script has no I/O, so all delivery still goes
through the outbox.

## D17. Room for several cooperating hubs
A likely setup is a cloud hub plus a hub at home without internet (Briar on the local Wi-Fi, a physical
Meshtastic node), connected through the mesh and through late Briar syncs.
**Decision:** not built in v1, but the design keeps room: messages carry a fingerprint (original author
label + normalized text), the config will list peer hubs and their accounts, messages from a peer are
parsed to their original author, and endpoints shared with a peer de-duplicate by fingerprint.
**Consequences:** the message model and the routing API include the author label, the fingerprint and
"relayed by a peer" from the start. Fingerprint collisions on short repeated texts are a known risk,
decided when the second hub is built.

## D18. The hub is an ordinary member of Briar groups
Managing Briar group membership from the hub (creating groups, inviting, re-creating them to cut
someone off) is complex, needs a bigger briar-headless patch, and makes the hub the only creator, so
members sync with it only.
**Decision:** people create and run Briar private groups in the Briar app. The hub joins them as a
member, accepting invitations only from contacts the admin added, and the admin lists them in the
config as endpoints. The hub can be in any number of Briar groups. Linking a Briar author to a member
is optional and only changes the label, by posting a code into a group. This supersedes the
membership parts of D6 and D15.
**Consequences:** the briar-headless patch shrinks to listing groups, accepting invitations, reading
and posting. Who can read a Briar group is outside chatko's control. The hub syncs a group with its
creator, and with other members only if they are its contacts and reveal it, so the README explains
how to add the hub as a contact.

## D19. mesh-api re-evaluated; D2 and D3 stand
The owner asked again whether to build on mr-tbot/mesh-api for its many extensions (Signal among them)
and whether the cloud hub needs a virtual node at all. On 2026-09-30 the core was one 9 561-line file
with no tests; extensions receive a plain string (`send_message(message: str)`), so the model is a star
around the mesh; one Telegram chat per extension; routing is JSON only; there are no groups, members,
outbox or cross-path de-duplication, and no virtual node. Its extensions are thin (Telegram 284 lines,
Signal 226 lines over signal-cli-rest-api). Publishing to MQTT without `meshtasticd` is feasible for
`channel` delivery, but `dm` would need our own PKI, NodeInfo, ACKs and retries.
**Decision:** keep our own hub (D2) and `meshtasticd` for virtual nodes (D3). Reuse external daemons
the way mesh-api does (e.g. signal-cli-rest-api for a later Signal extension).
**Consequences:** what mesh-api lacks (routing, identities, outbox, several hubs) is exactly our core.
Porting one of its extensions costs about one session.

## D20. v1 scope: one cloud hub with a virtual Meshtastic node
The owner wants the first working result to be a cloud hub that keeps a Briar group, a Telegram group
and Meshtastic (a channel or DMs to several nodes) in sync.
**Decision:** v1 implements only the virtual node (`meshtasticd` over TCP). A physical hub node (D13)
and several hubs (D17) are later work. The connection setting and the adapter port stay general so
that serial, BLE and TCP to a real radio can be added without changing the extension logic.
**Consequences:** no serial/BLE code, tests or hardware sessions in v1. A home hub waits for the physical
node and for the answer on briar-headless LAN sync.

## D21. Author labels come from the routing script, with a Latin default
The owner wants the author signature to be as programmable as routing.
**Decision:** the default author label is the member's nick; an unlinked author gets `~` and a 2–6
character Latin form of the name their network gives, made by the nick generator (not unique, not
stored). The routing script may define `label(author, target, ctx)` to replace this, per network or per
target, and can call `default_label`. Member records, nicks, node linking and PSK hand-out stay as
designed: they give a stable name per person, but relaying does not depend on them.
**Consequences:** unlinked Meshtastic nodes no longer show their short name (`~BC1`) by default. Labels
still count towards network limits, and the core trims them where needed.

## D22. The hub relays only; no member management in v1
The owner wants the simplest useful hub: a message arrives somewhere and is routed to other endpoints.
Managing people (Telegram as a membership source, member records, nicks in SQLite, a control surface
with `/nick`, `/mesh` and `/briar` commands, handing out PSKs, linking nodes by code) is not needed for
that.
**Decision:** v1 has no members, membership sources, control surface or commands. Who may write is
decided in each network (Telegram group admins, the Briar group creator, whoever has the channel PSK) and
in the config (`dm` node lists, Briar contacts). Channels and PSKs are set in the config. An optional
`people` section maps a label to accounts in several networks; otherwise the label is generated from the
network's display name (D21). Admin notices go into one configured endpoint. This supersedes D4 and the
member parts of D9 and D21; the rest of D9 (every network is an extension) stands.
**Consequences:** the core shrinks to endpoints, routing, labels, outbox and config; the extension API
has one protocol (`EndpointProvider`). SQLite holds only runtime state. Impersonation is possible where a
network allows it (a stranger named like a person gets a similar label); a script can mark accounts not
in `people`. A web UI or commands can be added later as new protocols.

## D23. The hub makes no Briar contacts; people add it to their groups
The owner wants Briar to work like Telegram: a person creates the group and adds the hub, and the hub
has no logic for contacts.
**Decision:** the hub never adds contacts. Briar still needs the person and the hub to be contacts
before an invitation, and a contact at a distance is made by both sides adding each other's link, so the
admin does the hub's side once per person by hand through briar-headless's REST API (documented in the
README). The hub accepts every group invitation automatically, posts an admin notice with the group id,
and ignores groups that are not in the config. This replaces the configured `contacts` of D18.
**Consequences:** less hub code. The hub syncs a group only with the creator by default, so messages
between the group and the hub pass through the creator's phone; more contacts (made by hand) and
"Reveal contacts" remove that bottleneck. Any contact the admin made can put the hub into a group, which
is harmless because unlisted groups are ignored.

## D24. The hub's Briar account creates the groups; `briarctl` manages it, outside chatko
With a person as the creator (D23), the hub syncs a Briar group only through that person's phone. A
creator that is online all the time is what keeps a Briar group alive, and only the cloud hub is.
**Decision:** the hub's Briar account creates the groups. Contacts, groups, invitations and
re-creating a group are done by `briarctl`, a separate command-line tool that calls the
`briar-headless` REST API. It is not a chatko extension, shares no code with chatko and is kept apart by
an `import-linter` contract. The chatko `briar` extension stays a relay: it reads and posts in the groups
listed in `chatko.yaml`. Joining a group made by someone else stays possible through
`briarctl invitation accept`. This supersedes D18 and D23 on who creates groups and who adds contacts;
the hub still does not manage members inside chatko (D22).
**Consequences:** the briar-headless patch again needs create, invite, members and dissolve (D6), used
only by `briarctl`. One more small program to build and test (session S27). Cutting someone off works as
in D15: dissolve, create, invite, then change the endpoint in the config.

## D25. The hub provisions its Meshtastic node over the admin API and is its only client
Spike S2 (`meshtasticd` 2.7.26) showed that `meshtasticd`'s YAML sets only the hardware (the simulated
radio), the MAC address (the node id) and logging. Names, region, channels, PSKs and the MQTT client
live in the node's own settings and are changed through admin messages. `meshtasticd` also serves one
API client at a time, and it turns `ignore_mqtt` on when `EU_868` is first set, which silently drops
every packet that crossed MQTT.
**Decision:** each `meshtastic` extension instance provisions its node from `chatko.yaml` at start (and
on config reload) through its own TCP connection: names, region, `ignore_mqtt` off, `config_ok_to_mqtt`
on, the MQTT client, and the channels with uplink and downlink on. It writes only what differs, one
admin message at a time, and reconnects after the node reboots. The node id is fixed by `MACAddress` in
the `meshtasticd` YAML, which lives in `config/`. The hub is the only API client of its node.
**Consequences:** `chatko.yaml` is the single source of truth for the node; changes made with the
Meshtastic app are overwritten at the next start. The admin inspects the node through the hub's logs, or
stops the hub first. The `meshtasticd` containers need a restart policy. Members' radios need "Ignore
MQTT" off (design.md §6.2), which goes into the setup instructions for members.

## D26. Meshtastic `dm`: keys from the config, delivery by the node's ACK
Spike S2, part 2 (`meshtasticd` 2.7.26) showed that a text direct message needs the public keys on
both sides, and that a node pins the first key it learns for another node and ignores NodeInfo with a
different one. Keys travel in NodeInfo, which a node sends at most once per 10 minutes and answers to
the same asker once per 12 hours, and keys learned in a node's first minute after start are not saved.
So a reset radio gets a new key that the hub's node ignores, and a hub node that loses its volume gets
a new key that every radio ignores. The hub's node reports an "implicit" ACK as soon as the broker
echoes a packet back, and a real ACK only when the destination answers; it retries twice and gives up
after about 23 s. It silently drops a text that comes less than 2 s after the previous one, and the
oldest of more than 4 admin messages waiting in its queue.
**Decision:**
- Each `meshtastic` instance may set `private_key` (a secret in `.env`); the hub provisions it, so the
  hub node's identity survives a lost volume or a move to another server. Recommended for production.
- Each instance may list `contacts`: node id → public key. The hub adds them to its node with
  `add_contact`, which stores them as favorites (saved at once, never evicted) and replaces a pinned
  key. A `dm` node whose key the hub's node learned by itself is made a favorite as well.
- A `dm` delivery counts as delivered only on an ACK from the destination node. `MAX_RETRANSMIT` is
  retried when the node is heard again; the key NAKs (`PKI_SEND_FAIL_PUBLIC_KEY`,
  `PKI_UNKNOWN_PUBKEY`) are retried after the nodes exchange NodeInfo; `NO_CHANNEL`, or a NodeInfo whose
  key differs from the pinned one, posts an admin notice about a key mismatch. A text without even the
  implicit ACK was dropped by the node and is retried.
- The adapter sends admin messages one at a time and waits for each response (this replaces the pause
  of D25), keeps at least 2 s between the texts of one hub node, and matches ACKs and NAKs to packets
  itself: the library forgets a response handler after the first response (the implicit ACK), and its
  node cache takes keys from NodeInfo that the node rejected.
**Consequences:** two more config keys and one more secret. A key change is a config edit, since the
hub has no commands (D22). The hub logs its node's public key at start, so the admin can hand it to
members. Members need "OK to MQTT" on and "Ignore MQTT" off (design.md §6.2).

## D27. The Kyiv mesh is EU_433 with a non-default primary PSK
Spike S2, part 3 decoded the channel URL on <https://meshtastic.kyiv.ua/join>: region `EU_433`, preset
`LONG_FAST`, channel 0 `LongFast` with its own 32-byte PSK, and a secondary channel `KyivUA`. The broker
`mqtt.meshtastic.kyiv.ua` refuses anonymous clients.
**Decision:** the example config uses `EU_433` and a `KYIV_PRIMARY_PSK` secret for the hub's primary
channel, which must match the mesh for NodeInfo, ACKs and `dm` to work (D26). We never hard-code the
community's PSK or credentials in the repository.
Part 3 also found that the broker issues credentials per node: the community answered the request for
a login for a virtual node by asking for a physical node to be confirmed in the registry first. A
claimed node gets a login equal to its node id and the root topic `node/<node id>`.
**Decision:** until a physical node is claimed, development and the first deployment use our own
Mosquitto (as in `lab/`); a physical node with an internet connection acts as the gateway between that
broker and the Kyiv mesh (D17). Claiming a node for the Kyiv broker is a later step with the owner.
**Consequences:** the `LongFast` source (§6.5) reads the mesh's primary channel, not the public default
one. The root topic, PKI and downlink policy stay open questions for the Kyiv community.

## D28. briar-headless starts unattended from secrets in `.env`
Spike S3, part 1: briar-headless reads its account from stdin, the nickname and password the first
time and the password on every start, because the password encrypts the database key. It has no
option for a password file. Its API token is the file `auth_token` in the data directory, made on the
first start if missing.
**Decision:** we run upstream briar-headless (built from a pinned tag with JDK 17, in a JRE image)
behind a small entrypoint that feeds stdin from `BRIAR_PASSWORD` (and `BRIAR_NICKNAME` when no
account exists) and writes `BRIAR_AUTH_TOKEN` into `auth_token`. Both secrets live in `.env` (D7);
chatko and `briarctl` use the same token. The lab version is `lab/briar/`; session S25 turns it into
the production image. A `--password-file` option could be offered upstream with the patch of D24,
which would make the entrypoint simpler.
**Consequences:** anyone with `.env` can open the hub's Briar account, as with the other secrets. A
backup of `data/` is useless without `BRIAR_PASSWORD`, so the password must be kept with the backups'
secrets. A wrong password makes the container exit and restart in a loop, which the logs show.

## D29. The private-group API is a small additive patch that we carry in our fork of briar-headless
Spike S3, part 2 read Briar 1.5.21: `briar-headless` switches private groups off in its core (a
feature flag), so it cannot even be invited; everything else is in Briar's shared code, which the
Android app calls through thin controllers; invitations and answers already reach headless's
WebSocket once the flag is on. Its WebSocket reaches only a connected client. Upstream has had an
open issue for joining groups in headless since 2019 (#1664), community merge requests for forum
endpoints have been open since 2024, and headless gets only dependency upgrades.
**Decision:**
- The patch turns private groups on and adds one `privategroups` package (controller, output
  functions, Dagger module), routes in `Router.kt` and README sections: the endpoints and events of
  design.md §7.4. It changes nothing in Briar's core or the app and adds no dependencies. It follows
  the upstream style and comes with unit and integration tests like the existing ones.
- Group ids in URL paths are URL-safe base64; `briarctl`, `chatko.yaml` and chatko's logs show Briar
  ids in that form, and the extension compares ids as bytes.
- The WebSocket forwards only other members' joins and posts. The extension catches up after every
  reconnect by listing each group's messages and submitting the unread posts of others, and marks each
  post read once the hub has stored it (design.md §7.2). Briar's read flag is used for nothing else.
- We keep our fork as a branch on top of each upstream release tag we use, build the image from it
  (S25), and offer the patch upstream as a merge request that refers to #1664, without depending on
  its merge. The LAN transport (a few lines, needed only by a home hub, D17) is a separate patch,
  made later.
**Consequences:** S24 implements the plan of spikes.md S3, part 2. Every Briar release we take means a
rebase of the patch, which stays cheap because it mostly adds files and touches four existing ones
(`HeadlessModule.kt`, `Router.kt`, the test module and the README). Once the patched peer runs, it
tells all its contacts that it supports private groups. This refines D6 and D24.

## D30. v1 reaches the mesh through our own broker and our own gateway; phase 0 is closed
Session S07 walked through every (verify) of the design. All that v1 relies on was answered by the
spikes S1–S3 or the upstream source. What stays open concerns only the Kyiv broker and the
community's gateways: what the broker does with packets under `node/<id>`, its PKI and downlink
policy, the gateways' firmware and settings, and how common "Ignore MQTT" is on relays. The broker
gives logins only to claimed physical nodes (D27), so the hub cannot use it in v1 anyway. The
firmware source confirmed that `EU_433` (and `UA_433`, `UA_868`) turns `ignore_mqtt` on like
`EU_868`, and that ESP32 gateways support MQTT over TLS without checking the server's certificate.
**Decision:**
- v1 runs its own Mosquitto in the hub's compose file. The hub's `meshtasticd` nodes connect to it
  inside the Docker network; our gateways (physical `EU_433` radios with internet, with the mesh's
  primary channel and the groups' private channels, uplink and downlink on) connect over the
  internet on port 8883 with TLS, each with its own user and an ACL limited to the root topic. No
  anonymous clients.
- Our gateway is the only path between the hub and the air in v1, for both `channel` and `dm`
  endpoints. Both stay first-class (D14): `channel` costs one packet, `dm` gives a per-node ACK.
- The open Kyiv questions move from (verify) marks to a list in design.md §6.6. They are answered
  when a physical node is claimed (roadmap S04) or in the field test (S23), and do not block v1.
- Phase 1 starts. The ARM64 images (`meshtasticd`, our `briar-headless`) are run for the first time
  in S25 and S29, because the hosting candidate is ARM64.
**Consequences:** the server opens one more inbound port (8883) and keeps a Mosquitto password file
and ACL; the deployment session (S29) covers them. The field test (S23) needs a physical `EU_433`
gateway node of our own. This refines D27, which already chose our own Mosquitto until a node is
claimed, and changes design.md §11 (inbound ports were SSH only).

## D31. Project tooling: uv and hatchling, strict static checks, the dependency rule as import contracts
Session S08 set up the code base. Choices that are not obvious from the files:
**Decision:**
- One distribution (`chatko`) with five packages under `src/`: `chatko`, the three extensions and
  `briarctl` (architecture.md §2). `uv` manages the environment and `uv.lock` is committed; CI runs
  `uv sync --locked`. `hatchling` builds. There are no runtime dependencies yet; each session adds
  those it uses.
- `ruff` runs a wide rule set (including `ANN`, `S`, `PL`, `TID` with all relative imports banned);
  `mypy --strict` covers `src` and `tests`. `lab/` (spike scripts with their own inline dependencies)
  and `docs/` are outside both.
- The dependency rule is ten `import-linter` contracts (architecture.md §7). Extensions may reach
  `chatko.extension_api` only, and indirect imports through it are allowed. Whether `application`
  may import `extension_api` and `routing_api` is deliberately not forbidden yet: S10 settles the
  shape of both APIs and then adds the contract that fits.
- The per-package coverage gates are `coverage report --include=… --fail-under=…` steps in CI, not
  custom code. The pre-commit hooks call the tools through `uv run`, so the versions are the lock
  file's.
**Consequences:** the first push is the first CI run on macOS and Windows. `routing.example.py` is
checked by `ruff` only until S12 tests it against the real `routing_api`.

## D32. The domain model: immutable values, two transliteration tables, a versioned fingerprint
Session S09 wrote `chatko.domain` (architecture.md §2.1). Choices that the design left open:
**Decision:**
- Entities are frozen dataclasses with validation in the constructor; a state change (a delivery
  attempt) returns a new value. One exception type, `DomainError`. No dependencies outside the
  standard library.
- An `AccountKey`'s kind is the extension type (`telegram`), not the instance (`tg`), so the
  `people` section names an account once for all instances. An `EndpointRef`'s name only has to be
  unique within its instance; how the config names sites is settled with the config (S14).
- `Topology` (groups, sources, people) enforces what routing relies on: an endpoint is the site of
  one group or one source, never both, and an account belongs to one person.
- Transliteration: every name is read as Ukrainian, by KMU-2010, with no separate Russian table
  and no language guessing (the owner's choice; the design had "Russian by a similar one"). Letters
  of other Cyrillic alphabets are added to the table (Russian ы э ъ, Serbian, Macedonian, Kazakh),
  and a letter with a diacritic falls back to its base letter (ё → e, ў → u). A label only has to be
  short and readable, not a correct spelling in the author's language.
- The label generator treats words as runs of letters, digits, apostrophes and hyphens, counts only
  Latin letters, and falls back to the short name and then the account id when the name gives fewer
  than 2 letters (design.md §8).
- The fingerprint is BLAKE2b with a 16-byte digest over the normalized default label and the
  normalized plain text (attachment placeholders included), with `chatko-fp-v1` as the hash's
  personalization, so a future change of the algorithm cannot match old fingerprints by accident.
  It uses the default label, not the `label` hook, so it does not depend on the target; the label
  keeps only letters and digits, so a `~` mark added by a script does not matter. A unit test pins
  one value, because hubs compare fingerprints with each other (D17).
- `ruff`'s confusable-character rules are off for the tests and the transliteration tables, where
  Cyrillic letters are data.
**Consequences:** S10–S12 build the public APIs and the pipeline on these types; the APIs may wrap
them but should not redefine them. Changing the fingerprint means a new personalization string and
a note for peers. Two hubs match fingerprints only if they label a person the same way (the same
`people` labels, or names that generate the same label).

## D33. A group's endpoints are its "sites", not its "legs"
The owner found "leg" an awkward term, in English and in Ukrainian ("нога").
**Decision:** an endpoint that belongs to a group is a **site** (Ukrainian "майданчик"): the
config key is `sites:`, the domain has `Group.sites`, `Group.has_site` and `Group.other_sites`. The
word is replaced in every document, the earlier decisions included, since only the term changes,
not what they decided. "Site" in the sense of a web page is now written "website".
**Consequences:** none beyond the rename; nothing was released with the old key.

## D34. The admin names every site and source; the core never reads an endpoint's settings
An endpoint's settings (`chat`, `group`, `channel`, `dm`) belong to its extension, so the core
cannot make a stable name for an endpoint from them. It needs one for the hub's state in SQLite
(outbox, de-duplication) and for the routing script. Two ways were weighed: each extension derives
a key from its endpoint config (`chat:-100…`), or the admin names the endpoints.
**Decision:** the admin names them. A group's `sites` is a mapping from a name to the endpoint
(`family: {sites: {tg: {ext: tg, chat: …}}}`), and the endpoint's name is `<group>.<site>`
(`family.tg`); a source's name is its key under `sources` (`longfast`). `EndpointRef(instance,
name)` carries that name, and no two endpoints of an installation share one. The core reads only
`ext` and passes everything else to the extension, which validates it with its
`endpoint_config_model` (architecture.md §3). This refines D32, which left the naming to S14.
**Consequences:** extensions need no way to serialize their endpoint config, and the routing script
refers to readable names. Renaming a site or moving it to another group changes its name, so the
hub's state for it (pending deliveries, de-duplication) no longer applies; changing a site's
settings under the same name (a new Telegram chat id) keeps its name.

## D35. The extension API: a lifecycle, one capability, recipients, ordered deliveries, versions
Session S10 settled `chatko.extension_api` (architecture.md §3), the decision that is most
expensive to change once third parties write extensions. Choices that the sketch left open:
**Decision:**
- **Layers.** The two public APIs sit between the domain and the application: they import only the
  domain, and the application imports them, because it calls extensions and the routing script
  through them and implements `HubContext` for them. They re-export the domain types their users
  need instead of redefining them (D32). The test kits are for tests only. This answers the
  question D31 left open, and the `import-linter` contracts say it as one layers contract.
- **Shape.** `Extension[C]` (lifecycle: `start`, `stop`; `type_name`, `api_version`,
  `config_model`) plus capability base classes, of which v1 has one, `EndpointProvider[E]`
  (`endpoint_config_model`, `set_endpoints`, `recipients`, `deliver`, `delivery_report`). Config
  models are pydantic models (now a dependency), and the classes are generic in them.
- **No I/O before `start`.** The constructor and `set_endpoints` only record and validate, so the
  core checks a new config by running them on a fresh instance (`check-config`, every reload) and
  a running instance only gets valid input. `set_endpoints` takes the whole ordered set instead of
  attach/detach calls: the extension sees config order (a node in several `dm` endpoints goes to
  the first) and applies the difference itself. An instance whose own section changes is replaced.
- **Recipients.** An endpoint may list recipients that it reaches separately (the nodes of a `dm`
  endpoint). The core makes one delivery per recipient, so each node has its own ACK, retries and
  state in the outbox, and the Meshtastic extension keeps no delivery state of its own. Routing
  targets may narrow the recipients (D36).
- **Deliveries.** `Delivered(truncated)`, `Retry(reason, after)` or `Failed(reason)`; an exception is
  a `Retry`. Per endpoint and recipient, one call at a time, oldest first, and a `Retry` holds back
  the newer messages, so they never overtake each other. `hub.retry_now` ends a wait early (a radio
  heard again, D26). Delivery is at least once; `OutboundMessage.attempt` lets an extension check
  whether an earlier attempt got through. When a delivery ends, the source's extension gets a
  `DeliveryReport` (the ✂️ reaction of design.md §6.3).
- **`HubContext`** has `submit` (returns once stored, safe to cancel), `heard` (for `last_heard`,
  any packet counts), `retry_now`, `notify_admin` (rate-limited by key, never raises) and `now`. The
  key–value store of the sketch is left out: no v1 extension needs it.
- **Versions.** Each API has `API_VERSION = (1, 0)` and `is_supported(version)`: the same major,
  a minor no newer than the core's. A minor version only adds (a field with a default at the end,
  an optional method, a `HubContext` method, a new capability); a major one breaks. An extension
  declares the version it was written for, and the core refuses one it does not support.
- **Contract suite.** `extension_api.testing` has `ExtensionContract` (pytest tests inherited by a
  `TestXxxContract` class), a `ContractDriver` that each extension writes over the fake of its
  network port, `FakeHub`, and `FakeNetwork` with `FakeExtension`, the reference extension that the
  suite runs against and the core's tests will use. A run against twelve deliberately broken
  variants of `FakeExtension` failed each of them.
**Consequences:** S11 implements `HubContext`, the per-recipient fan-out and the ordered outbox; S15
checks `is_supported` at discovery and uses `FakeExtension`; every extension session ends with the
contract suite. The domain gained `Delivery.recipient`, `Target.recipients`, `Topology.endpoint`
and `plain_text`. Adding a recipient-level concept later (e.g. SMS to several phones) needs no API
change. A chat-relay extension that cannot tell its own posts apart, or cannot give a post a stable
id, cannot pass the suite: both are needed against echoes and duplicates.

## D36. The routing API: a read-only view, a message-aware `label` hook, recipients in targets
Session S10 also settled `chatko.routing_api` (architecture.md §4).
**Decision:**
- `RoutedMessage` is a read-only view of the stored domain `Message` plus its group, so it
  cannot drift from the domain. `RoutingContext` is one concrete class that the core and the test
  kit both build from a `Topology`, the instances' extension types, the endpoints' recipients and a
  `RoutingHistory`; only the history differs, so a script behaves the same in its tests as in the
  hub. The history (`last_heard`, `seen`) is served from memory: the script is synchronous and may
  not wait for I/O. `seen` counts only messages stored before the one being routed.
- The `label` hook is `label(msg, target, ctx)`, not `label(author, target, ctx)` as design.md had
  it: with the whole message a label can depend on where it came from (a feed signed with the node's
  short name). A target's own label wins over the hook.
- `to_endpoint(…, recipients=…)` narrows a delivery to some of an endpoint's recipients (none for an
  empty list), and `ctx.recipients(endpoint)` lists them; together with `last_heard` this makes the
  "no DM for a radio heard on the channel lately" rule of design.md §6.2 a few lines of script
  (`routing.example.py`).
- The context also gives `endpoint(name)` (D34 names), `extension_type(endpoint)` and `now`.
  `default_label(author, ctx)` and `mirror(msg, ctx)` take the context so that the defaults may
  depend on the installation later without a new signature.
- A script may declare `api_version`; the engine runs the defaults for one it does not support
  and tells the admin. Peers (D17) come later as `ctx.peers`, a minor version.
**Consequences:** S12 builds the engine on these types, adds the fake installation builder and
assertions to `routing_api.testing` (it has `FakeHistory` now) and tests `routing.example.py`, which
already type-checks against the API. S11 keeps the last-heard times and recent fingerprints in
memory for the history.
