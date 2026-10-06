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

## D37. The pipeline routes before it stores; the outbox runs one lane per endpoint and recipient
Session S11 wrote the application layer's pipeline, invariants, outbox worker and `HubContext`
(architecture.md §5). Choices that the design left open:
**Decision:**
- **Route, then store.** design.md §9.1 stored the message before routing it. The pipeline now
  routes first (the script is synchronous and has no I/O) and stores the message with all its
  outbox rows in one transaction (`MessageRepository.add`), which refuses a copy with the same
  endpoint and transport id. A crash therefore never leaves a stored message without its rows,
  which a resubmission could not repair, since it would be dropped as a copy.
- **One message at a time.** The pipeline handles submissions in order under a lock, so the outbox
  keeps the order of arrival across endpoints and `seen` counts exactly the earlier messages. The
  hub's traffic is small; routing is in memory. `submit` runs the pipeline in its own task, so a
  cancelled caller does not leave half a message.
- **Attempts are counted before the call.** `Delivery.begin_attempt` is saved before `deliver`, and
  the outcomes no longer count attempts. After a crash in the middle of an attempt, the next one
  has `attempt == 2`, which is what D35 promised extensions. This changes the domain of D32.
- **Lanes.** The worker keeps the pending deliveries per endpoint and recipient in memory, with a
  task per non-empty lane, and loads them from the outbox on `start`. Backoff 10 s doubling to 1 h,
  no jitter (few deliveries, no thundering herd); a `deliver` or `delivery_report` call times out
  after 2 min (longer than a Meshtastic DM's ~23 s of retries); a delivery to an instance that is
  not running is a `Retry`, so an instance being restarted loses nothing.
- **Giving up** is by age, not by attempts: once the message is 3 days old the next `Retry` fails
  it. `retry_now` can cause many attempts for a radio that comes and goes, so a count would be
  arbitrary; age is what makes a message stale.
- **Fingerprint de-duplication** is per endpoint with its own window (`Installation`), checked
  against the in-memory history (7 days). A copy is stored (its transport id is then known) but not
  routed.
- **Own posts** stay the extension's job (§9.1 step 1): the core has no way to tell the hub's own
  accounts in v1. The contract suite checks it, and an end-to-end test with `FakeExtension` checks
  that what the hub posts never comes back in.
- `notify_admin` keys are prefixed with the instance, so two instances' notices are rate-limited
  apart. Calls about another instance's endpoint are logged and ignored.
- The application's fakes are in `chatko.application.testing`. The import contract for test kits
  became a `protected` one: the `forbidden` contract it replaced let a package import its own kit.
  `hypothesis` (dev only) runs the property tests of the invariants.
**Consequences:** S12 replaces `DefaultRouter` with the routing engine behind the same `Router`
port. S13 implements `MessageRepository` and `OutboxRepository` in SQLite (the store must keep the
order of the rows) and should persist and reload the `HubHistory`; S14 configures the
de-duplication windows and possibly `OutboxSettings`; S15 builds the `Installation` snapshots and
starts the worker before the extensions. Open for the owner: a direct message from one node of a
`dm` endpoint does not reach the endpoint's other nodes (design.md §9.3).

## D38. A direct message from one node of a `dm` endpoint reaches its other nodes
D37 left open that the invariant "never back to the endpoint it came from" kept a `dm` endpoint's
nodes from hearing each other: a direct message from one node went to the group's other sites, but
not to the other nodes of the same endpoint, and no script could change that. The core could not
narrow the rule to the sender, because only the extension knows which recipient a post came from
(that a node id is both the author and the recipient is Meshtastic's business). The owner chose to
let the radios hear each other.
**Decision:**
- `InboundMessage.from_recipient` (optional, at the end): at an endpoint with recipients, the
  recipient that posted the message. The domain `Message` stores it and `RoutedMessage` shows it.
- The invariant excludes only that recipient of the source endpoint; the others may get the
  message. If the extension does not name the sender, the whole source endpoint stays excluded.
- `mirror` adds the source site narrowed to its other recipients, so this is the default; a script
  can narrow or drop it (airtime: N−1 direct messages with ACKs for N nodes).
- The contract suite checks that an extension names the recipient that posted at an endpoint with
  recipients; `FakeExtension` reads a place with recipients only from them.
- The API version stays `(1, 0)`: nothing is released yet. After a release this would be a minor
  version (a field with a default at the end).
**Consequences:** the Meshtastic extension (S21) sets `from_recipient` to the sending node for `dm`
endpoints. The SQLite schema (S13) stores it with the message.

## D39. The routing engine: a script source port, per-function fallbacks, a test kit shaped like the config
Session S12 wrote the routing engine (architecture.md §5.3) and the admin's test kit
(architecture.md §4). Choices that the design left open:
**Decision:**
- **The script's file is behind a port.** The application reads the code from a
  `RoutingScriptSource` (`FileScriptSource` in the infrastructure, reading in a thread; a fake
  in `application.testing`) and runs it itself with `compile` and `exec` as a module of its own.
  The engine compares the code with what it read last, so a reload of the same code changes
  nothing and a refused script is reported once; watching the file for changes is the config
  watcher's job (S14), which calls `reload()`.
- **What a script must define is part of the routing API.** `RoutingScript.of` (in
  `routing_api`) checks `route`, the optional `label` and `api_version`, including that the
  functions take the hook's arguments, so a `label(author, ctx)` copied from `default_label` is
  refused at load time instead of failing on every message. The version is checked first. The
  engine and the test kit use the same check, so a script that passes its tests loads in the hub.
  `API_VERSION` and `is_supported` moved to `routing_api.version` (the package re-exports them).
- **Each function falls back on its own default.** A failing `route` sends that message by
  `mirror`; a failing `label` gives that target `default_label`, while the rest of the script
  keeps working. Wrong return values (no list of targets, a blank or non-string label) count as
  errors, so they never reach the pipeline (`Delivery` refuses a blank label).
- **Once per error kind** means per function, exception type and script line, until the script
  changes: the admin learns of each bug once, with its line, and a fixed script starts fresh.
- **Notices in tasks.** `route` and `label` are synchronous and run under the pipeline's lock, so
  the engine does not await its notices there but posts them in tasks of its own. The `Router`
  port stays synchronous.
- **The test kit takes the installation in the shape of `chatko.yaml`** (`FakeInstallation`:
  extension instances, groups with `{site: instance}`, sources, recipients, people) and routes a
  message as the hub would, labels included. It shows what the script asked for and does not
  apply the invariants, which belong to the application layer that the API may not import; it
  raises the script's errors so that a test shows their tracebacks. `routing.example.py` is now
  type-checked by `mypy --strict` and tested this way in CI; its test is the template for an
  admin's `config/test_routing.py`. Tests may ignore `ARG001`, like the example, because fake
  scripts keep the hooks' signatures.
**Consequences:** S14 watches `routing.py` with the YAML and calls `RoutingEngine.reload()`;
`check-config` uses `load_script` (it must report a `ScriptError` as an invalid config) and runs
the admin's tests, which needs `pytest` at run time (an optional extra, to be decided in S14).
S15 creates the engine with a `FileScriptSource` for the configured `routing` file, calls
`reload()` before the extensions start and passes the engine to the pipeline as its router.

## D40. SQLite: one connection, explicit transactions, migrations in code, history as a journal
**Status:** accepted (S13).
- **One connection and a lock.** `Database` shares one `aiosqlite` connection (autocommit mode,
  `BEGIN IMMEDIATE` per use) among the repositories, and every call is a transaction under an
  `asyncio.Lock`. The awaits inside one transaction (a message and its deliveries) cannot
  interleave with another coroutine's statements, which would otherwise commit half of it. The hub
  is one process with one writer, so this costs nothing.
- **Migrations are a tuple of scripts** (`PRAGMA user_version`), each applied with its version in one
  transaction. A released script is never edited. A newer database is refused, not guessed at.
- **Stored forms.** Times are UTC text of fixed width, so that they sort and compare as text; the
  author (account, person, relayed label) is a JSON snapshot in the message row, so a message reads
  back as it was even after the config changes; a delivery's missing recipient is `''` so that the
  unique key (message, endpoint, recipient) works. The order of the outbox is an autoincrement `seq`.
- **`HubHistory` stays synchronous and in memory; persistence is a journal.** The routing script
  reads the history inside `route`, which is synchronous, so the history cannot await a database.
  It remembers what is unsaved (`take_unsaved`), and `HistoryPersistence.flush` writes that through
  `HistoryRepository.record`; a failed write is retried with the next changes. `load` restores the
  last-heard times and the arrivals within the retention. A crash loses at most what came after the
  last flush, and the cost is one more message that is not recognised as a copy.
- **Pruning.** `MessageRepository.prune(before)` removes messages received before `before` that
  have no pending delivery, and their deliveries (`ON DELETE CASCADE`); their transport ids stop
  counting as copies, so the retention must be longer than any network's replay window (S15 uses
  7 days like the history, configurable in S14). `HistoryRepository.prune` removes old arrivals but
  keeps the last-heard times, which are one row per account and endpoint.
- **`AccountRegistry`** is a port of its own: `note` says whether the hub sees the account's key for
  the first time, which is what the admin notice for new accounts (design.md §8) needs. The notice
  itself is S14/S15.

**Consequences:** S15 creates the `Database` at the configured path under `data/`, passes the
repositories to the pipeline and the worker, loads the history at start, and calls `flush` after each
message and on a timer, and the prunes daily. `aiosqlite` is a runtime dependency.

## D41. The config: a core model that leaves endpoints to their extensions, staged errors, notices through the outbox
**Status:** accepted (S14).
- **Shape.** `chatko.application.config.validate_config(raw, types)` turns the parsed YAML into an
  immutable `Config` or raises one `ConfigError` with a line per problem, each starting with its place
  in the file (`groups.family.sites.tg.chat`). The core's pydantic models reject unknown keys; an
  extension instance's section (without `type`) goes to its `config_model` and an endpoint's settings
  (without `ext`) to its `endpoint_config_model` (D34). Errors never contain values, because a value
  may be a secret (pydantic is asked for no input in its errors, and a YAML error gives only its
  line and column). Errors come in stages: sections and endpoints first, then the checks that need the
  topology (`admin_notices.to`, the de-duplication names), and last the constructor and `set_endpoints`
  of every instance on a fresh object with an inert hub, so an extension's own refusal (a channel the
  node does not have) is reported as `extensions.<instance>`.
- **New core keys.** `fingerprint_dedup_s: {endpoint name: seconds}` is the per-endpoint window of D37
  (a top-level mapping, not a key inside the endpoint, because the endpoint's keys belong to its
  extension); `retention_days` (default 7, D40); `peers` is parsed into account sets and not used
  yet. `admin_notices` is optional: without it a notice is only logged.
- **`${ENV}`.** Substituted after the YAML is parsed, in values only (never keys), at any depth, so
  a value cannot change the YAML's structure. A variable that is unset **or empty** is an error that
  names its place; `$${` is a literal `${`. Outside block context YAML needs the value quoted
  (`['${A}']`). `chatko check-config` also reads `--env-file` (default `.env`, the environment wins) so that it
  works from a shell; the running hub gets its environment from Docker.
- **`ConfigService`** reloads on demand and keeps the last valid `Config`. Identical content is
  `UNCHANGED` (a refused file too, so a bad file is reported once), a refusal tells the admin with the
  key `config:load` and leaves `current` alone, and a different problem is reported again.
  `refresh()` reloads the config and then the routing script, which is all a watcher callback needs.
  The file watching is `infrastructure.watcher.watch_files` over `watchfiles`: it watches the
  directories, so replacing a file by rename or creating the routing script later is noticed.
- **`AdminNotifier`** posts a notice as a message of its own from a pseudo endpoint
  (`chatko:hub/notices`, in no extension, so no delivery report goes anywhere) with one delivery
  per recipient of the admin endpoint, so it has the outbox's retries and survives a crash. It
  rate-limits per key (10 minutes by default) and tells how many notices it held back. Without an
  endpoint, or when storing fails, it logs and never raises, because it is called from error paths.
- **`check-config`** validates the config, loads the routing script named in it with `load_script`
  (a named script that is missing is an error here, while the running hub takes it for no script),
  and runs the admin's `test_*.py` next to the config in a `pytest` subprocess, so that their
  imports and failures stay out of the checker. `pytest` is the optional extra `chatko[test]`; without
  it the tests are skipped with a message, not failed. Exit code 1 for anything wrong.
- **Discovery** (`infrastructure.discovery`) moved forward from S15 because `check-config` needs the
  installed types. It refuses, with a reason and without stopping the rest, an extension that cannot
  be imported, is no `Extension`, is registered under another name than its `type_name`, or needs an
  unsupported API version.

**Consequences:** S15 wires `ConfigService`, the notifier and `watch_files`, builds `Installation`
snapshots from `Config`, and decides what to do with a `LOADED` outcome while the hub runs. A
reload that changes an endpoint's name drops its pending state (D34); `ExtensionSetup` equality is
how S15 knows an instance must be restarted. `PyYAML` and `watchfiles` are runtime dependencies.

## D42. The hub is one application object over its ports; the worker runs inside the extensions' lifetime
**Status:** accepted (S15).
- **`HubRuntime` is in the application layer.** It builds the services over `HubPorts` and runs the
  extension instances, so the whole hub (start, reloads, restarts of instances, stop) is tested with
  the in-memory fakes. The composition root, `app.run`, only puts the infrastructure under it, watches
  the files and handles signals. `chatko run` keeps its state in `data/chatko.sqlite3`.
- **The worker starts after the extensions and stops before them**, the reverse of D37's advice. The
  worker should call only started instances: started first, it would answer every pending delivery
  with "not running" and a backoff at each restart. Nothing is lost: every message is stored before
  its rows are enqueued, and the worker loads the outbox when it starts. Stopping it first also
  guarantees what §3.1 promises, that no delivery is in progress when an instance stops. While the
  hub runs, an instance is stopped only after it is out of service (`Installation.running`) and its
  attempts in progress have ended (`OutboxWorker.finish_attempts`).
- **`Installation` knows the instances that do not run.** The recipients and types of a constructed
  instance are known while it starts or restarts, so a message routed meanwhile still gets one row
  per recipient; only running instances get deliveries and reports. When an instance has started,
  what waits for it is due at once.
- **A reload swaps the routing script and the topology in one step of the event loop.** The runtime
  stops the changed instances, reloads the script, and then, without awaiting, gives the new endpoint
  sets, constructs the new instances and publishes the new snapshot. A message is never routed by a
  new script against the old topology or the other way round. This needs the runtime to interleave the
  two reloads, so `ConfigService.refresh` (D41) is gone and the runtime's `refresh` does it. The
  script's file follows the config (`ConfiguredScriptSource`), and the watcher moves to a new file.
- **Deliveries to an endpoint the config no longer has fail** ("… is no longer in the config"), which
  is how D34's "drops its pending state" happens.
- **An instance whose `start` raised is tried again with the next config that loads**, not on a
  timer: by the extension API, `start` raises only when the instance cannot work at all, and what
  fixes that is a change of its config.
- **The history is saved every 10 s and at stop**, not after each message as D40 planned. A
  message's transport id is in SQLite with the message anyway; a crash loses at most 10 s of
  fingerprints and last-heard times, and the hub saves a transaction per message.
- **New accounts.** `ExtensionHub` hands the author of every stored message and every account heard
  to `NewAccounts`, which notes it in the `AccountRegistry` and, with `admin_notices.new_accounts`,
  tells the admin of a key seen for the first time that belongs to no person (key `account:<key>`).

**Consequences:** the hub runs (`chatko run`), but no extension is registered yet, so a real config
waits for S17. A change saved in the moment between the start and the watcher's first watch is not
noticed until the next save, because `watchfiles` does not say when it watches. Extensions get
`set_endpoints` while they run, and `stop` only after their deliveries ended. Tests may write files
inside async tests (`ruff`'s `ASYNC240` is off for `tests/`).

## D43. Phase 1 review: notices take one snapshot, the core survives its inputs
**Status:** accepted (S16).
Session S16 reviewed the whole core (`/code-review` at high effort over `src/chatko` and
`routing.example.py`, the coverage and the import contracts). Its ten findings are fixed, each
with a test that failed first. Choices that the fixes made:
- **An admin notice is a message of one snapshot.** The notifier took its endpoint from the
  latest config but the endpoint's recipients and the topology from the published `Installation`,
  which lags while a reload stops instances; at start the routing script is loaded before the
  first snapshot. A notice could get a delivery without the recipient of a `dm` endpoint, or one to
  an endpoint "no longer in the config". Now `Installation.admin_endpoint` is part of the snapshot,
  and the routing engine posts its load refusals in tasks, as it already did its errors, so a
  refusal during a reload goes out with the snapshot published in the same step as the script's
  swap. While a reload stops instances, notices go to the admin of the config in effect.
- **Calls into a stopping instance include its delivery reports.** `finish_attempts(instance)` also
  waits for the reports to the instance from other lanes, so `stop` comes after every call into
  it, as architecture.md §3.1 promises.
- **A transaction is rolled back also when it is cancelled while it begins or commits.**
  `aiosqlite` runs a statement whose await was cancelled anyway, so a cancelled `BEGIN` left the
  shared connection in a transaction and every later one failed. The outbox's stop and the
  runtime's timers cancel tasks that may be in a transaction; the drained submissions and the last
  history flush at shutdown were lost.
- **The config's numbers are bounded.** `retention_days` is at most 3650 (`now - retention` must
  stay a date), and a `fingerprint_dedup_s` window is positive and no longer than the retention,
  since `seen` cannot look further back than the fingerprints are kept (this also refuses NaN and
  infinity). The config file must be UTF-8: anything else is a `ConfigError` with the line, like a
  YAML error, instead of a traceback; so is a character YAML does not allow.
- **A routing script's `exit()` is a script error**, at load time and in `route` and `label`
  (`SystemExit` is caught with `Exception`; `KeyboardInterrupt` still stops the hub).
- **Discovery refuses an extension class without a `type_name` or an `api_version` of the form
  `(major, minor)`**, instead of failing on it.
- **The backoff stays at its longest wait** however many attempts there were (a `dm` node heard
  often makes many attempts; the power overflowed after 1024).
- `check-config` runs only the `test_*.py` files next to the config, as documented.
**Consequences:** `AdminNotifier` no longer takes a `target`; the runtime publishes the admin
endpoint with each snapshot. Coverage is back to 100 % except one defensive line. Left as they
are, for their low impact: a delivery whose instance is not running counts as an attempt, so the
extension may see `attempt > 1` for a message it never got; a routing script in a directory that
does not exist when the hub starts is watched only after `chatko.yaml` changes; an extension that
answers `Retry(after=0)` every time is retried at once every time. Phase 1 is closed; S17 starts
phase 2.

## D44. The Telegram extension: long polling behind a port of its own, groups followed to their new id, ✍ for a cut message
**Status:** accepted (S17).
Session S17 wrote the Telegram extension (`chatko_telegram`, architecture.md §3.7, design.md §5).
Choices that design.md left open:
- **A port in the extension's own terms.** `TelegramApi` has the five calls the extension needs and
  its own event and error types; `AiogramTelegramApi` is the only module that imports aiogram. The
  extension is tested against `FakeTelegramApi`, and the adapter against a scripted aiogram HTTP
  session, so aiogram's own reading of Bot API answers is tested too. The extension owns its
  polling loop (`getUpdates`, for `message` and `my_chat_member` only) instead of aiogram's
  dispatcher, so that it confirms an update only once the hub has stored its message.
- **The bot's id comes from its token** (`<bot id>:<secret>`), so the extension knows its own posts
  without a call to Telegram, and `start` does no I/O that could fail. Telegram does not hand a
  bot its own messages anyway; the check is there for the contract (D35).
- **The transport id is the message id**, which Telegram keeps unique within a chat, and an update
  is handed over again until it is confirmed, so messages survive a restart.
- **Leave only when added.** The bot leaves a group or channel when it is added to one that is not
  in the config (`my_chat_member`), as design.md §5 says. Messages from a group that it is in but
  that is not in the config only make an admin notice: the group may be a configured one that
  became a supergroup, and leaving it would cut the group off.
- **Supergroups are followed.** When a group becomes a supergroup (seen as a service message in
  either chat, or as the error of a delivery to the old id), the extension maps the endpoint to the
  new id until the hub restarts and asks the admin to change the config. Writing the config is the
  admin's (the hub never writes it, design.md §10). A supergroup that another endpoint already has is
  not taken over.
- **Errors the admin can fix are retried, with a notice.** A bot removed from a chat, blocked by a
  person, or refused by Telegram (a revoked token, another program polling with it) answers
  `Retry` and tells the admin, so messages wait in order for the fix instead of being lost; they
  are given up after 3 days like any other (design.md §9.1). Only a request Telegram rejects as
  such (a 400 that is not "chat not found") is `Failed`. Flood control's wait becomes `Retry.after`.
- **A cut message gets ✍, not ✂️.** Bots can react only with Telegram's standard reactions, and ✂️
  is not one of them. ✍ ("write it shorter") is the closest. If a chat does not allow it, the
  failure is only logged.
- **Long texts are cut, not split.** Telegram takes 4096 UTF-16 units per message; a longer text
  (only a Briar post can be one) is cut with `…` and reported as truncated. Splitting would repeat
  the parts already sent on every retry, since delivery is at least once.
- **One update that aiogram cannot read does not stop the polling.** aiogram's models require every
  field of the Bot API version it was generated for; an answer it cannot read as a whole is read
  update by update, and an unreadable update is confirmed without an event and logged. Otherwise it
  would be handed over again forever.
- The `/start` that the app sends when a person presses Start is dropped in private chats; edits
  and service messages are not relayed (design.md §8).
**Consequences:** `aiogram` (with `aiohttp`) is a dependency of the hub, and `telegram` is
registered in the `chatko.extensions` entry point group. `config.example.yaml` is tested against
the real Telegram models. Telegram's limits on posting (about 20 messages a minute in a group) are
met only through flood control's `Retry`. The live test (S18) should check how a group's setup
for the hub (privacy mode, the bot as admin) changes its id, and whether ✍ reads well.

## D45. The Telegram bot stays in chats that are not in the config, and needs no admin rights
**Status:** accepted (S18). Changes design.md §5 and D44's "leave only when added".
The live test (S18) showed the order in which Telegram reports a group becoming a supergroup, all
in one second: the bot added to the new supergroup (`my_chat_member`, left → member), then
`migrate_from_chat_id` in the supergroup, `migrate_to_chat_id` in the old group, and the bot's
promotion if that is what caused it. The extension saw a supergroup that was not in the config and
left it before it learned that the supergroup was its own group. The bot came back only because the
promotion, still on its way, re-added it.
- **The bot never leaves a chat.** Waiting a while before leaving a supergroup would have fixed the
  order, but leaving protects little: the admin notice comes anyway, the hub drops a foreign chat's
  messages without storing them, and a mistyped chat id in the config would make the bot leave the
  admin's own group. In a chat that is not in the config, the bot ignores the messages and the first
  one makes an admin notice with the chat's title and id, once per chat until the config changes
  (the notifier's rate limit alone would repeat it every 10 minutes). Being added to such a chat
  is only logged, so a migration does not make a notice of its own. `TelegramApi.leave_chat` is gone.
- **Strangers are kept out by BotFather**, not by the hub: `/setjoingroups` → Disable once the
  groups are set up.
- **The bot needs no admin rights.** With privacy mode disabled before it joined, the bot as a plain
  member got every message of a basic group. Making it an admin, even with no rights, turned the
  group into a supergroup with a new id. The hub follows such a change (D44), but the setup no longer
  asks for it.
Other results: ✍ on a message that another network got only in part reads as "it was cut"; the
admin notices in the owner's private chat read well; the default label of a one-word Telegram name
(`Sergi`) is the name itself.
**Consequences:** design.md §5, `config.example.yaml` and architecture.md §3.7 say so. The phase 2
goal is now "foreign groups are ignored and reported".

## D46. The Meshtastic adapter: a port of connections, the library without its reconnect, a node object that owns the rest
**Status:** accepted (S19).
Session S19 wrote the base of the Meshtastic extension (`chatko_meshtastic`, architecture.md
§3.8): the `MeshApi` port, its adapter over the `meshtastic` library and its fake, the
provisioning, and `MeshNode`. They are tested against a fake of the port, the adapter against a
node that speaks the stream protocol at the other end of a socket pair, and both together against
the lab (`pytest -m lab`). Choices that D25, D26 and architecture.md §3.6 left open:
- **The port is a factory of connections.** `MeshApi.connect` returns a `MeshConnection` that
  carries what the node told it on connect (`NodeState`: number, settings, node database) and
  ends when the node closes it; `events` ends with it. A reboot, a crash and another client look
  the same: the connection ends, and `MeshNode` makes a new one. The port's types are its own
  (`Packet` with `Text`, `Routing`, `NodeInfo` or `Other`; admin commands such as `SetLora`), so
  only `library_api` imports the library.
- **The library's own reconnect is switched off by overriding its hooks.** `TCPInterface`
  reconnects on its own when the node closes the socket, and that races with ours (D25). The
  adapter subclasses it and overrides seven hooks of meshtastic 2.7 (`myConnect`, `_readBytes`,
  `_writeBytes`, `_reconnect`, `_handleFromRadio`, `_disconnected`, `_waitConnected`), so that a
  closed connection simply ends, waiting for the config stops when it does, and the adapter reads
  every message from the node as it comes. The library is pinned below 2.8; a new minor version is
  taken once the lab tests pass with it. The library's pubsub topics and its node cache are not
  used: the node database is read from the messages of the connect (D26).
- **Threads.** Each connection has one thread of its own for the blocking calls (connect, send),
  so they keep their order and one that hangs (the library waits forever for room in the node's
  queue) holds up nothing but its dead connection; closing frees such a call. The reader thread
  hands packets over with `call_soon_threadsafe`. A cancelled connect closes what its thread
  opens.
- **`MeshNode` owns everything else.** It connects (backoff 1 s doubling to 30 s), provisions on
  every connection, sends admin messages one at a time and waits up to 10 s for each answer (no
  answer: connect again), waits for the reboot after a commit, adds the contacts once the settings
  match, and only then is ready; `send_text` raises `NotReadyError` before. It matches every
  answer to its request id itself, keeping answers that come before the hub has the packet id of
  what it sent. A broadcast is delivered by the implicit ACK; a direct message only by the
  destination's ACK, the implicit one setting `reached_broker`; any NAK ends the wait. It keeps
  `min_send_interval` between texts, hands the packets of other nodes to the extension through a
  queue of their own (a slow handler holds up no ACK), and keeps the node database as the node
  does: a NodeInfo with another key than the pinned one, or with none, changes nothing.
- **Provisioning writes only what differs, as the config says.** The names, the LoRa settings
  (twice when the region changes, for the firmware's `ignore_mqtt`), the private key if
  configured, the MQTT client with the node's login, and every channel slot: the configured ones
  with uplink and downlink on, the others off. The primary channel is written with the name from
  the config (`LongFast`), which gives the same MQTT topic and channel hash as an unnamed one. A
  contact keeps the names the node already has for it. The MQTT broker is `host`, or `host:port`
  when the port is not the default for the TLS setting, the form the firmware reads.
- **What it tells the admin:** a node unreachable for 2 min (once per outage); a node that closed
  five connections in a row within a minute of making them (another client, D25); a refused
  setting or contact; settings that still differ after the reboot that saved them, after which it
  goes on with the node as it is instead of rebooting it again. It logs the node's public key when
  the node is first ready.
- **The lab's broker is set up like production (D30):** no anonymous clients, a user per node
  (`hub`, `radio`) and one for the scripts (`lab`), each limited by an ACL to `msh/lab/#`.
  `lab/mosquitto/start.sh` makes the password file at start; the lab passwords are
  `chatko-lab-<user>`, lab values like the lab's PSK and keys.
**Consequences:** `meshtastic` (with `protobuf`, `pypubsub`, `bleak`, `pyserial`) is a dependency
of the hub, and `types-protobuf` of development. S20 registers the extension, adds the endpoint
models and builds the `channel` endpoints on `MeshNode`; S21 the `dm` endpoints, including the
favorites for listed nodes and the key-mismatch notices, from `MeshNode.node` and the NAKs. A text
handed over while the node is rebooting is lost to the node; the outbox retries what got no ACK.
A half-open TCP connection (the node gone without closing it) is noticed only when a write fails;
inside the Docker network this does not happen.

## D47. Meshtastic `channel` endpoints: parts sent one at a time on the broker's echo, a retry goes on from the part that failed
**Status:** accepted (S20).
Session S20 registered the Meshtastic extension (`meshtastic`, architecture.md §3.8) and wrote its
`channel` endpoints on `MeshNode` (D46). Choices that design.md §6.2–6.4 left open:
- **One endpoint model for both kinds.** `MeshtasticEndpoint` is either `channel: <name>` or
  `dm: [node ids]`, so that `config.example.yaml` is checked against the real extension now. A
  `channel` must be one of the instance's channels, and two endpoints cannot share one (each
  channel's texts go to one endpoint). Until S21, a `dm` endpoint lists its nodes as recipients,
  a delivery to it fails ("not relayed yet") and a direct message to the hub is logged and
  dropped: failing is quieter than retrying for three days, and nobody runs v1 yet.
- **The transport id is `<node id>/<packet id>`.** Spike S2 found `(from, id)` stable across
  gateways, and the hub's node hands a packet over once. Ids are not unique forever, but the core
  keeps transport ids only for the retention (7 days), and a radio's ids do not repeat that soon.
- **A part goes out once the one before was echoed.** Each part is a broadcast that asks for an
  ACK; the implicit ACK (the broker's echo, D26) is what confirms it, within 25 s (the firmware
  gives up after about 23 s). The extension remembers, in memory, how many parts of each message
  got through to an endpoint, so a `Retry` sends only the rest; after a restart it sends them all
  again (at least once). It keeps that for 1024 deliveries at most.
- **A delivery waits up to 10 s for the node** when it is connecting or provisioning (a commit
  reboots it), instead of answering `Retry` at once and waiting for the backoff; `on_ready` asks
  the hub for the deliveries waiting for the instance's channels.
- **`heard` at most once a minute per node and place**, by the hub's clock: a busy primary
  channel sends many packets, and each `heard` writes the account to SQLite. A packet is heard at
  a `channel` endpoint only when it is a decoded broadcast on that channel: the channel field of a
  packet the node could not decrypt is a hash, and a direct message has none.
- **Labels and parts.** A label is cut to 39 bytes (a node's longest long name) with `…`. A text
  that does not fit `NatAda: text` in 200 bytes is split at whitespace into up to 3 parts
  `NatAda (n/N): …`, each filled as far as it goes, and the last one ends with `…` if the text
  goes on; a word longer than a part is cut between characters (not grapheme clusters).
- **Tapbacks are not messages.** The Meshtastic apps send a reaction as a text with the `emoji`
  flag; the port's `Text` carries it as `reaction`, and the extension drops it, as the Telegram
  extension ignores reactions.
**Consequences:** S21 adds the `dm` path to the same extension: direct messages from listed
nodes (first endpoint wins), deliveries per node by the destination's ACK (D26), `retry_now` for
a node when it is heard again, favorites, key-mismatch notices. The parts and the remembered
progress apply to direct messages as well.

## D48. The extensions live in `src/extensions/`, the admin's tool in `src/tools/`; the example names the instance `telegram`
**Context:** the extension packages sat beside the core in `src/`, so the tree did not show which
packages are the core and which are plug-ins. The example config also named the Telegram instance
`tg`, an abbreviation where every other name is a word.
**Decision:**
- `chatko_telegram`, `chatko_meshtastic` and `chatko_briar` moved to `src/extensions/`, and `briarctl`
  to `src/tools/`. They stay top-level Python packages (there is no `__init__.py` in either
  directory), so no import changes and the import-linter contracts hold as they are. This refines
  D31 (five packages under `src/`) and D35.
- `hatchling` builds the core from `packages` and takes the others with `force-include`, so the wheel
  has the same five top-level packages as before; `dev-mode-dirs` makes an editable install see
  them. `mypy_path` and `ruff`'s `src` list the new directories, coverage's `source` lists each package
  directory (a directory without `__init__.py` is not a source of its own), and CI's gates use the new paths.
- The example config names the instance `telegram` and shows, commented out, that an extension type
  can have several instances (a second bot, a second Meshtastic node). Nothing in the code limits the
  instances of a type (design.md §2).
**Consequences:** a branch that edits files under the old paths merges through git's rename detection.

## D49. Meshtastic `dm` endpoints: the node's ACK per part, retries woken by what the hub hears, favorites kept by the node object
**Status:** accepted (S21).
Session S21 wrote the `dm` endpoints of the Meshtastic extension (architecture.md §3.8) on
`MeshNode` (D46) and the parts of D47. Choices that D26 and design.md §6.2 left open:
- **One path for both kinds.** A delivery to a `dm` recipient renders the message like a channel
  one (≤ 3 parts of ≤ 200 bytes) and sends each part as a direct message on channel 0, the next
  once the node's ACK came within 30 s (`MeshtasticTimings.ack`; the node gives up after about
  23 s). The parts that got through are remembered per endpoint, message and recipient, so a retry
  to one node goes on where it stopped and the other nodes are not affected.
- **What a delivery without the node's ACK waits for** (design.md §6.2 has the table): the
  extension keeps, in memory, per node and `dm` endpoint, whether the deliveries wait for the node
  to be heard (`MAX_RETRANSMIT`, or the broker's echo and nothing more) or for the hub's node to
  have its key (`PKI_SEND_FAIL_PUBLIC_KEY`), and calls `retry_now` for that node when a packet
  from it shows it (any packet; a packet after which the node has a key). Every result is a
  `Retry` without `after`, so the outbox's backoff (up to 1 h) stays the fallback for a radio the
  hub does not hear, e.g. one with "OK to MQTT" off. `PKI_UNKNOWN_PUBKEY`, `NO_CHANNEL`, other
  NAKs and a text the node dropped are left to the backoff: waking on the radio's own NAK packet
  would retry at once, before the hub's NodeInfo got there, and a key mismatch needs the admin.
  Losing these hints in a restart loses nothing: when the node is ready, the extension asks for
  the waiting deliveries of all its endpoints. This is the only delivery-related state the
  extension keeps besides the parts (D35 still holds: the outbox owns the deliveries).
- **Heard at the `dm` endpoints.** A packet addressed to the hub's node (a direct message, an ACK,
  a NAK) from a listed node is heard at every `dm` endpoint that lists it, not only the first: a
  script that checks `last_heard` at the second one should see it too. A direct message itself
  goes to the first endpoint only, with `from_recipient` set to the node (D38).
- **Only PKI direct messages come in.** A direct message encrypted with a channel key could come
  from anyone with that key under any node id, and the primary key of the Kyiv mesh is public; the
  firmware drops such messages from radios anyway (spike S2), so the check costs nothing.
- **Favorites are `MeshNode`'s job.** The extension gives it the nodes of its `dm` endpoints
  (`keep_favorites`, at start and on every `set_endpoints`). While the node is ready, a task of
  `MeshNode` sends `add_contact` with the key and names the node already has for each such node
  whose key is not a favorite yet: at once after connecting, and whenever it takes a NodeInfo of
  one of them. It is the only sender of admin messages after provisioning, so they still go one
  at a time; one without an answer makes it connect again, as in provisioning. It sends only the
  key the node pinned, so it never replaces one.
- **Key-mismatch notices:** on `NO_CHANNEL` from a radio, and on a NodeInfo from a node of a `dm`
  endpoint with another key than the pinned one (the notice names the new key, to be checked with
  the radio's owner). One per node (key `key-mismatch:<node id>`), until the node acknowledges a
  message again or the instance restarts, instead of one per retry and hour for three days. A
  missing key gets no notice: it is the normal state of a new radio until its NodeInfo comes.
- **The fake node answers like `meshtasticd`:** `FakeMeshApi.naks` gives a destination the NAK it
  gets (from the hub's node for `MAX_RETRANSMIT` and `PKI_SEND_FAIL_PUBLIC_KEY`, the latter
  without the broker's echo; from the radio otherwise), and the fake makes its key pair when a
  region is first set, as the firmware does.
**Consequences:** the contract suite runs over both kinds of endpoint. Asking a radio for its
NodeInfo after `PKI_SEND_FAIL_PUBLIC_KEY` (a port command the hub does not have) is left out:
the throttles of spike S2 make it work only now and then, and `contacts` covers the need. An ACK
that comes after the node gave up (`MAX_RETRANSMIT`) is not counted, so on a slow real mesh a
radio may get a message twice; S23 shows whether that happens. S22 tries the extension in the
lab, radio ⇄ Telegram, with both kinds of endpoint.

## D50. Lab integration tests run the whole hub, with Telegram faked, nightly from fresh volumes
**Status:** accepted (S22).
Session S22 tried the Meshtastic extension in the lab (Mosquitto and two `meshtasticd` nodes,
`lab/`) as part of the running hub (`tests/integration/test_meshtastic_lab_relay.py`):
- **The whole hub, with Telegram in memory.** The tests run `run_hub`, the composition root of
  `chatko run`, with real SQLite and config files, the Meshtastic extension on the lab's hub node
  and the Telegram extension over `FakeTelegramApi`. A real bot would need a token and a network
  in CI, and the Telegram side was tried with a real bot in S18 (D45); here the radio side is
  real. The lab's radio node plays a member's radio under a `MeshNode` in the test. Two groups,
  a Telegram chat with the lab's private channel and one with a `dm` endpoint of the radio, cover
  both kinds of endpoint, each way, with the parts of a long message and Telegram's ✍.
- **Retries wait an hour in these tests**, so a message that reaches the radio after a `Retry`
  proves that the extension asked for it (`retry_now`), not the outbox's backoff.
- **The tests provision the nodes themselves.** The lab's private keys are fixed, so their public
  keys are constants of the tests, and the radio gets the hub's key as a contact like
  `provision.py` gives it; the tests pass on wiped volumes. Tests that change the lab (the radio's
  container stopped, the radio given another key and the hub's config the matching contact) put
  it back.
- **Nightly in CI** (`.github/workflows/lab.yml`, and on demand): the lab from fresh volumes, all
  `lab` tests (15, about 2.5 min), the containers' logs on failure. Not on every pull request
  (architecture.md §7): the lab needs Docker and minutes, and a pinned `meshtasticd` beta.
- **What the lab showed:** relaying works both ways with both kinds of endpoint. A direct message
  from Telegram had the radio's ACK 0.1 s after it was posted; with the radio's container
  stopped the hub's node gave up with `MAX_RETRANSMIT` about 24 s after the post, and the
  message reached the radio within a second of the radio being heard again. A radio given a new
  key answered `NO_CHANNEL` at once; the admin got one notice in Telegram, and once the config
  had the new key the hub reloaded, added the contact and delivered the waiting message within a
  second. A radio restarted less than 10 minutes after its last NodeInfo sends none at boot (the
  throttle of spike S2 survives restarts), so the hub hears it only when it sends something; the
  test's radio sends a text on the primary channel, which is no endpoint, and that wakes the
  delivery.
- **At least 2.5 s between texts, not 2.** The long message's third part was lost now and then:
  the node had dropped it for its 2 s limit, though the hub sent it 2 s after the second. The
  firmware measures the 2 s when it handles a text from its API client (`PhoneAPI`, firmware
  2.7.26), which can be a few hundred milliseconds after the hub sent it, and a dropped text does
  not restart its count. Thirty texts to the lab's node at each spacing: 5 dropped at 2.0 s,
  every other one at 2.05 and 2.1 s, none at 2.25 s (60 texts) and 2.5 s (90 texts). So
  `min_send_interval_s` must be at least 2.5 (it was 2, the limit itself; the example keeps 4).
  The node's NAK for it (`RATE_LIMIT_EXCEEDED`) goes to node 0 and never reaches the hub (spike
  S2), so a dropped part is retried after the outbox's backoff.
**Consequences:** a radio that comes back quietly gets its messages at the outbox's backoff (up
to an hour) or when it next sends anything (a text, telemetry, a position, its NodeInfo after
10 minutes), as design.md §6.2 says. S23 checks the same paths on the Kyiv mesh, with real
airtime and a gateway in between.

## D51. The private-group API as built: every id checked, one transaction per change, the phone confirms no migration
**Status:** accepted (S24).
Session S24 built the plan of D29 (spikes.md S3, part 2) in our fork of briar-headless: the
branch `1664-private-group-api` on `release-1.5.21` in `~/Projects/briar`. It turns private
groups on in `HeadlessModule.kt` and adds the package `privategroups` (controller, output
functions, Dagger module), the routes in `Router.kt` and the README sections; nothing in Briar's
core, no new dependency. The choices made while building it:
- **Every group id in a path is checked against the hub's private groups**, and anything else is
  a 404: `removePrivateGroup` would remove any group (a forum, a contact's conversation), and
  reading another client's group as a private group fails deep in Briar. Marking a message read
  also checks that the message is in that group, because Briar's message tracker would count it
  against the wrong group otherwise.
- **One write transaction per change.** A post reads the hub's previous message and the group's
  latest time, signs and stores; an invitation takes its timestamp and auto-delete timer, signs
  and sends. The app spreads both over its executors; in one transaction two requests at once
  cannot both chain to the same previous message.
- **Invitations need the creator.** Listing who can be invited and inviting answer 403
  `NOT_CREATOR` for a group the hub did not create (Briar's sharing status assumes the creator's
  session), and inviting answers 403 `NOT_SHAREABLE` with the contact's status for anyone not
  `shareable`. Inviting returns the contact's new status; accepting, declining, leaving and
  dissolving answer an empty 200, like the existing alias and delete routes.
- **Messages are listed and sent to the WebSocket in one form**, `groupId` included, joins
  without `text`; the hub's own messages never reach the WebSocket.
- **Revealing contacts is in the patch after all** (D52): `POST
  /v1/groups/{groupId}/members/reveal`. The member list names the contact of every member who is
  a contact of the hub (Briar names only the visible ones), a visible member is left as it is,
  and a reveal Briar refuses because one is under way is ignored, as the app does.
- **JSON fields are typed strictly**: `contactId` a JSON integer, `accept` a boolean, an optional
  `text` absent, null or a non-empty string.
- **Tests in upstream style**: 75 new tests (unit tests with mockk and JSONAssert, integration
  tests against a real peer, its WebSocket included); with the 99 existing ones, 174 pass in about
  40 s. Sixteen deliberate mutations of the controller each failed a test. A second integration
  test class needs a port of its own (`IntegrationTest` got a port parameter): the API server of
  a finished class keeps its port, because stopping it ends the process.
What the checks showed (2026-10-02):
- **No migration.** A phone (Briar 1.5.21) showed the hub, running upstream, as "not supported" in
  the invitation screen; after the switch to the patched peer on the same data, the phone could
  invite it as soon as they were connected again (10 s). Between two headless peers the same
  switch took about 4 minutes, the time Tor needed to connect them again.
- **With the phone, every endpoint and event worked** both ways: a group the phone created
  (invitation event and list, accept, joins and posts as events, posts from the hub, members,
  mark read, the phone dissolving it: `GroupDissolvedEvent`, then `DISSOLVED` for posts) and a group
  the hub created (invite, the phone's acceptance and posts, the hub dissolving it). Declining, a
  member leaving (the hub then sees it as `sharing` and cannot invite it again) and revealing were
  checked between headless peers.
- **A dissolve reaches the hub only when the creator next syncs with it.** The phone dropped its
  connection about a minute after its last message, so a check must wait for the event, not the
  clock.
- `lab/briar/` builds the image from the fork: the build context `briar` is a checkout of it,
  `../briar` next to chatko by default or `BRIAR_SRC` (a path or a Git URL; the upstream tag's URL
  builds the unpatched peer). The build needs BuildKit (`docker-buildx`).
**Consequences:** S25 publishes the fork, builds the production image from it and offers the
patch upstream, reveal included. S26 builds the extension on these endpoints and events, and S27
`briarctl` (with `group reveal`, D52). Every Briar release we take means rebasing the branch.

## D52. People create the Briar groups and invite the hub; the hub reveals its contacts; a group the hub creates is the fallback
**Status:** accepted (S24).
D23 had a person create each group and invite the hub, the way Telegram works. D24 made the hub's
account the creator instead, because a member syncs a group only with its creator and with
the members who are its contacts and have revealed that, so the hub would reach a person's group
only through the creator's phone. In S24 the owner chose the Telegram way again: a person creates
the group and adds the others and the hub; a group created by the hub is the fallback for when
that does not work.
**Decision:**
- The recommended setup (design.md §7.1): a person creates the group in the app and invites the
  hub. The admin joins the hub with `briarctl invitation accept`; the hub does not accept
  invitations by itself (D24 stays on who manages the account).
- The patch gets `POST /v1/groups/{groupId}/members/reveal` and `briarctl` gets
  `group reveal <group> <contact>…`: the hub reveals its relationship with the members who are its
  contacts, so it syncs the group with them directly, not only through the creator's phone. In
  S24, after a reveal, a post went from the hub to such a member with the creator stopped.
- `briarctl group create`, `invite` and `dissolve` stay, for the fallback.
**Consequences:** a member who is not the hub's contact exchanges messages with the hub through
the creator, or through another member who is a contact of both and has revealed the
relationship. Only the creator can invite and dissolve, so cutting someone off is done on the
creator's phone. If the creator leaves Briar, nobody can be invited any more, and the fallback (a
new group made by the hub) is the way out. This changes the recommendation of D24, not its split
between the relay and `briarctl`.

## D53. One briar-headless image for the server and the lab, built from the fork for amd64 and arm64
**Status:** accepted (S25).
D28 started briar-headless unattended in the lab; D29 and D51 made our fork, which S25 had to
package for the server and offer upstream.
**Decision:**
- The image lives in `deploy/briar/` and is the only one: `deploy/docker-compose.yml` runs it as the
  service `briar` (state in `data/briar`, API on `127.0.0.1:7000` like the node's on 4413, since
  chatko runs on the host for now), and `lab/briar/` builds the same Dockerfile. Gradle runs once on
  the build machine for both architectures; the runtime stage picks the jar for the target, so an
  arm64 image builds on an amd64 machine with QEMU only for that stage.
- The entrypoint starts as root only to give the data directory to the unprivileged `briar` user
  (a bind mount that Docker creates belongs to root) and runs again as `briar` with `setpriv`. It
  unsets `BRIAR_PASSWORD` and `BRIAR_AUTH_TOKEN` before starting Java, which has already read them
  on stdin and from `auth_token`. The image has `curl` for a health check: the API answering 401
  without a token. Tor is not part of the check, because it connects on its own later.
- `deploy/setup.sh` makes `BRIAR_PASSWORD` and `BRIAR_AUTH_TOKEN` in `.env` like the other secrets
  and links `.env` as `deploy/.env`, where Compose reads it for the `briar` service's variables;
  the container gets only the Briar variables, not the whole `.env`.
- The source is the build context `briar`, a checkout or the Git URL in `BRIAR_SRC`. No registry
  yet: the image is built where it runs, or copied with `docker save | docker load`.
- The upstream merge request is the branch as it is: upstream `master` had no commits after
  `release-1.5.21` (2026-10-05). `docs/briar-merge-request.md` holds its text, the checklist and
  the steps; the owner submits it from a fork on code.briarproject.org, which also becomes the
  fork's public home.
What the checks showed (2026-10-05): upstream CI's headless tasks pass (174 tests); the lab's
existing account signs in on the new image; a fresh account on a root-owned bind mount is created,
the token from `.env` opens the API, and a restart signs in again; the arm64 image under QEMU
creates an account, bootstraps Tor (an aarch64 binary) and creates a group and a post. The health
check turns healthy about 25 s after the start, under QEMU as well.
**Consequences:** the server needs Docker with BuildKit and the fork's source (or a copied image).
Once the fork is public, `BRIAR_SRC`'s default can be its URL. S29 runs the image on the ARM64 host
for real and backs up `data/briar` with `BRIAR_PASSWORD` kept among the backups' secrets.

## D54. The Briar extension: catch-up after every connection, read flags as the hub's note, the hub never accepts invitations
**Status:** accepted (S26).
S24 built the private-group API and D52 settled who creates groups; S26 is the extension that reads
and posts in the groups the admin lists.
**Decision:**
- One task per instance: `subscribe` (the WebSocket is open and authenticated before anything
  else, so no event can fall into a gap), then a catch-up of every configured group, then events.
  Any failure (a lost connection, a refused token, a post the hub could not take) closes the
  stream and starts again with a backoff of 1 s doubling to 60 s, which includes a new catch-up.
- A post is "handed over" when `hub.submit` returned, and only then marked read in Briar
  (design.md §7.2). A failed mark leaves the post unread; the next catch-up submits it again and the
  hub drops the copy by its transport id, the Briar message id in URL-safe base64.
- The overlap of a catch-up and the WebSocket is also cut in the extension, by a bounded memory
  (4096 ids) of the posts handed over. The hub's de-duplication stays the rule; the memory only saves
  it work.
- The hub's own posts are recognized by `authorStatus` `ourselves`, never submitted. Joins are not
  relayed; a post with only white space is marked read and dropped (the core refuses an empty
  message).
- A group the hub is not a member of (invitation not accepted, or left) is reported to the admin
  once, with the `briarctl invitation accept` hint, and messages for it `Retry`: they wait, in order,
  until the admin accepts. A dissolved group is reported once and its posts `Failed`: the group does
  not come back, and held messages would block newer ones for good. Its unread posts are still
  handed over.
- Posts longer than 31 744 bytes are cut with `…` and delivered as `truncated` (a post is a
  single message, nothing else sends so much text).
- The extension never accepts an invitation, whatever the group (D24, D52).
**Consequences:** a hub that was down for days submits every unread post of its groups at the next
start, oldest first. A post marked read by another client of the same account is lost to the relay;
the account has no other client. Whether the extension also runs against a real `briar-headless`
and a phone is checked in S28.

## D55. `briarctl`: a separate synchronous tool with its own client, settings from the environment, a question before anything that cannot be undone
**Status:** accepted (S27).
D24 and D52 set what `briarctl` does; S27 builds it as `src/tools/briarctl`, a package of the same
distribution and the `briarctl` command.
**Decision:**
- It shares no code with chatko or `chatko_briar`: its own Briar ids, records, errors, `BriarClient`
  port and `httpx` client (synchronous, since a command makes a few requests and exits), tested
  against `FakeBriarClient`. Two `import-linter` contracts keep it apart in both directions; a
  forbidden import in either direction broke them when tried. The duplicated code is about forty
  lines of ids and the endpoint calls, which is less than what a shared package would cost.
- Settings: `--url` or `BRIARCTL_URL` (default `http://127.0.0.1:7000`), the token from
  `BRIAR_AUTH_TOKEN` or `--token-file`. There is no `--token`, because an argument is visible in the
  process list. An address with a user name or password is refused. No error shows the token.
- Ids and arguments: a group is an id in either base64 form and is always printed URL-safe, ready for
  `chatko.yaml`. A contact is its id, or its alias or name when exactly one contact has it.
- `--json` prints the tool's own records (`snake_case` keys, ids URL-safe), so a script can pass them
  back. The text output is for people and may change.
- A command for several contacts (`group reveal`, `group invite`) goes on after one fails, reports
  each, and exits with 1; a failure of the whole call prints one line on stderr and exits with 1.
  Wrong arguments and settings exit with 2.
- `contact remove` and `group dissolve` ask first, `--yes` skips the question, and without a terminal
  to answer (end of input) they do nothing. A dissolve or a leave cannot be undone, and neither can a
  removed contact.
- One addition to design.md §7.5, found in the lab: a contact whose link was wrong or whose other side
  never added the hub's link stays "pending" for two days and then "failed", and the API's only way to
  drop it is `DELETE /v1/contacts/add/pending`. So `contact list` shows the pending contacts and
  `contact remove` stops adding one (by alias or id) when no real contact has that name. Nothing else
  was added: no `reveal` for every member at once, no group names as arguments.
**Consequences:** the tool works against the same `briar-headless` API as the extension and needs
its own README steps (`deploy/README.md`, `lab/README.md`). `lab/spike_briar.py` stays as the spike
client. Whether the whole flow works with a phone, a group a person made, is S27's last check and S28's
first step.

## D56. The three-network test runs the whole hub on the lab with a third node; nothing in the code changed
**Status:** accepted (S28).
D20 sets v1: one hub that syncs a Briar group, a Telegram group and Meshtastic as a channel and as DMs
to several nodes. S28 tried that on the lab, with the owner and their phone.
**Decision:**
- The "cloud setup" of the roadmap is the lab on the owner's machine: the real Telegram bot and group,
  the lab's `briar-headless` with the group "Chatko test" that the phone made (D52), and the lab's
  Meshtastic nodes. `chatko run` ran on the host, as a person would run it on a server; only the
  addresses differ (`127.0.0.1` instead of compose service names). The server itself is S29.
- The lab gets a third node, `radio2` (`!c4a7b003`, port 4405, its own Mosquitto user), because direct
  messages "to several nodes" need two. `provision.py` already gave every node the others' keys. The
  lab tests still use `hub` and `radio` only, so CI starts only those services.
- The config and routing script are kept in `lab/three-networks/` (the Telegram chat and Briar group
  are placeholders), so the test can be repeated. Its routing script is the example's rule: a radio heard on
  the channel within the hour gets the channel's copy only, the others a direct message.
**Result:** all of it worked at the first run, with no code change. A Briar post made earlier by the
phone (still unread) was caught up at start and went to Telegram, the channel and both radios by
direct message, each with its ACK. Texts from `radio` and `radio2` on the channel, and direct
messages from both to the hub, reached Telegram and Briar (and the channel or the other radio as the
script chose). A Telegram message and a Briar message from the owner's phone reached the channel and
the other network, and the owner saw everything on both. A clean stop (SIGTERM) took under two
seconds. The only warning was the broken pipe of a node that rebooted to save the settings the hub gave
it (a known pitfall, lab/README.md). The Briar extension is now tried against a real `briar-headless`.
**Consequences:** v1's phase 4 is closed. What is not tried: a Briar group with more than one other
member, a Telegram message lost to a restart of the hub mid-delivery (covered by tests with fakes and the
outbox), and anything on a server (S29).


## D57. Production is one compose stack with the hub in a container; a daily stop-and-copy backup of `.env`, `config/` and `data/`
**Status:** accepted (S29).
D30 and D53 put Mosquitto, the hub's `meshtasticd` and `briar-headless` in `deploy/`; the hub itself
still ran from a checkout. S29 finishes the stack and sets how it is backed up.
**Decision:**
- The hub is a service of `deploy/docker-compose.yml`, built by `deploy/chatko/Dockerfile`
  (`python:3.12-slim`, dependencies installed from `uv.lock`, the same file for amd64 and arm64).
  It runs as the host user (`CHATKO_UID` and `CHATKO_GID`, set by `setup.sh`) rather than root, so
  `data/chatko` stays the operator's own; `config/` is mounted read-only. It gets only the secrets
  its config names, as an explicit `environment` list (not the Briar password or the gateways'
  passwords), and a test checks that the list covers `config.example.yaml`. The build context is
  the repository, so `.dockerignore` keeps `.env`, `config/` and `data/` out of the image.
- `setup.sh` also makes `MESH_KYIV_PRIVATE_KEY`, the hub node's identity, as D25 and the README advise:
  the node then keeps its identity when its volume is lost, and members' radios keep trusting it.
- One backup archive holds `.env`, `config/` and `data/`. Briar's data is useless without
  `BRIAR_PASSWORD` (D28), and the node's identity is a secret, so the backup has to carry `.env`
  and is therefore secret itself: mode 0600 in a 0700 directory, and encrypted by the operator when
  it leaves the server. The script does not encrypt or upload: that depends on where the operator
  keeps backups, and a script that guesses wrong gives false comfort.
- The copy is **cold for the hub and `briar-headless`**: they stop, are copied and start again, a few
  seconds a day. SQLite in WAL mode (D40) and Briar's H2 database are not safe to copy while open, and
  the alternatives (SQLite's backup API run inside the container; an online backup of Briar's H2
  database, for which briar-headless has no API) cost more code than a short outage costs messages: the hub's inbound sources replay (Telegram long polling keeps updates for 24 h, Briar's
  read flag catches up, D54) and the outbox keeps what it had. `meshtasticd` and Mosquitto stay up:
  the node's files are written atomically and the hub provisions it again; Mosquitto has no state.
- `restore.sh` checks that the archive is a chatko backup before it touches anything, stops the
  stack, **moves** what exists to `replaced-<time>/` instead of deleting it, unpacks with numeric
  owners (the containers' users are not in the host's `/etc/passwd`) and restores the
  `deploy/.env` link, so it works on a new server with nothing but Docker and a clone.
- Both scripts are POSIX `sh`, run as root by the timer, and take their `docker compose` command from
  `COMPOSE`, which the tests replace with a stand-in that records calls. The whole of it was also run
  for real: a backup of a running stack, an empty tree, a restore, and the same Briar link and node key
  after the start.
**Where it runs:** the owner's VPS, which is also the development machine, so no cloud VM was made
(the Oracle Cloud candidate of D30 is dropped). Production lives in `/opt/chatko` as a snapshot of
the repository without `.git`, with the hub as the system user `chatko`, and is updated by copying the
changed files and `up -d --build chatko`. The development checkout keeps its own `.env` and
`config/` with new secrets and names, the compose project `chatko-dev`, other host ports, and no
Telegram token: the production bot has one hub (two hubs polling one token take updates from each
other), and a development bot comes later. The lab and its briar-headless (port 7000) stay as they were,
so production's briar-headless is published on 7001.
**Result (2026-10-05):** a stack started from `setup.sh` and a config in about 45 s; a client over TLS
on the published port was refused without credentials, with a wrong password and in plain text, and
published inside its root topic only; the public address worked from the machine itself; a backup
took 3 s of downtime and 25 MB (10 MB with a fresh Briar account); after a restore onto an empty
tree, once of a scratch stack and once of production's first real backup, the Briar link, the
node's public key and SQLite integrity were the same. The ARM64 images of `chatko` and `meshtasticd`
ran under QEMU (`briar-headless` did in S25).
**Not done:** a physical gateway connecting (the field test, S23), a check of port 8883 from another
network, and copies of the backups off the VPS (the timer has a line to adapt; the owner chooses
where). The Mosquitto certificate is self-signed, since gateway firmware does not check it (D30); a
changed address means a new certificate.
**Consequences:** a server needs Docker, the repository, the briar-headless image (built for the
server's architecture until the fork has a public home, D53) and `deploy/setup.sh`.
Updating is `git pull` and `up -d --build chatko`. A new `${NAME}` in `chatko.yaml` needs a line in
the service's `environment`.

## D58. v1 reaches the mesh through the Kyiv broker and the owner's claimed physical nodes, not through a gateway of our own
**Status:** accepted (2026-10-05, after S29); replaces the "own broker and own gateway" path of D30.
D30 chose our own Mosquitto and a physical gateway node of our own, because the Kyiv broker gives
logins only to claimed physical nodes (D27). The owner said there will be no gateway of that kind:
several physical nodes will sit in the Kyiv mesh and only send and receive our messages over LoRa, and
at least one of them, with MQTT on, is claimed with the community and forwards to the hub's virtual node
on the server.
**Decision:**
- The hub's `meshtasticd` connects to `mqtt.meshtastic.kyiv.ua:1883` with the claimed node's login
  (`KYIV_MQTT_USER`, its node id in hex; `KYIV_MQTT_PASSWORD`) and the root topic `node/<that id>`, the
  same as the physical node's. `config.example.yaml` has this as the default and shows our own
  Mosquitto as the alternative.
- Our own Mosquitto stays in the stack (the lab and the tests are built on the same configuration, and
  gateways of our own remain possible) but is not part of the path. Its TLS port is published on
  `127.0.0.1`; `MQTT_TLS_BIND=0.0.0.0` opens it. The server's only inbound port is SSH.
- Until the login exists, production keeps the node on our own Mosquitto, with the Kyiv `mqtt:` line
  ready as a comment in `config/chatko.yaml` and `KYIV_MQTT_*` empty in `.env`: a variable that is empty
  makes the config invalid (D41), and a node that tries the Kyiv broker without a login would only
  be refused every few seconds. The Meshtastic endpoints are configured but carry nothing.
**Consequences:** what was "open questions, not needed for v1" in design.md §6.6 now decides whether
`channel` and `dm` work: whether the broker passes packets between clients under one `node/<id>` root,
its PKI and downlink policy, and the owner's node having the group's private channel. The field test
(S23) is the first connection and fills in spikes.md. The community may still decline a bot node.
Nothing in the code changed: only the example config, the compose file's port binding and the docs.

## D59. v1 sends to Meshtastic only as direct messages
**Status:** accepted (2026-10-05, the owner).
D20 and S28 had Meshtastic as a private `channel` and as `dm` to several nodes. The owner does not want
channel broadcasts for now.
**Decision:**
- v1's sites for Meshtastic are `dm` lists. `config.example.yaml` has no private channel and no `channel`
  site (they are comments), `routing.example.py` is the plain "all other sites" rule with the feed, the
  tag and the labels, and `docs/deployment.md`'s check is a direct message.
- `channel` endpoints stay in the extension, its tests and the lab (S28's `lab/three-networks/` still
  uses both), so nothing is removed; turning one on is a config change.
**Consequences:**
- A gateway needs only the primary channel (`LongFast`, the Kyiv PSK) with uplink and downlink on. No
  private PSK has to be given to people or to the gateway, so any node with MQTT on the hub's broker is
  a gateway, not only ours (D58).
- A direct message is confirmed by an ACK and retried (S21): a gateway that is away costs delay, not a
  lost message. Each listed node gets its own message of three packets (text, ACK, ACK of the ACK),
  which is the cost for many members on a shared `LongFast`.
- The question of §6.6 whether the Kyiv broker carries PKI direct messages between clients now decides
  whether the Meshtastic side of v1 works at all (the field test S23 answers it).
- `MESH_FAMILY_PSK` is still made by `deploy/setup.sh` and passed to the container, for when a `channel`
  endpoint is wanted; the example config no longer uses it.


## D60. Production holds the real Briar account and the first group; development and the lab start from new ones
**Status:** accepted (2026-10-05, the owner).
S29 set production up next to development with every secret generated anew, while the one account that
carried real state, the lab's briar-headless (a contact, an invitation to the people's group), stayed in
the lab. The owner's rule: what is real lives in production, and development is the one that is made new.
**Decision:**
- Production's briar-headless runs the account that used to be the lab's (its data copied to
  `data/briar`, its `BRIAR_PASSWORD` and `BRIAR_AUTH_TOKEN` in production's `.env`). Its contact and the
  invitation came with it; the hub joined the people's group «Кризовий чатко» and revealed its contacts.
  What was replaced is kept in `/opt/chatko/replaced-<date>/` (the empty account and the old `.env`).
- The lab's briar-headless (`lab/briar`, port 7000) was recreated: a new account, new secrets in
  `lab/briar/.env`, the old volume removed. The same account must never run in two places.
- Production's `groups` has one group, `crisis`: the Telegram supergroup «Кризовий Чатко» and that Briar
  group, with the default routing. Meshtastic joins when the Kyiv broker works (D58, D59).
**Consequences:**
- The hub's Briar link is the one the people already have; nothing is to be re-added.
- The Briar account's nickname stays `chatko-lab`: the nickname is set only when an account is created.
- Tested by the owner both ways, Telegram → Briar and Briar → Telegram.


## D61. The login belongs to the claimed node's id; the hub's own node id and keys stay its own, and the node's keys are made new before it is claimed
**Status:** accepted (2026-10-05, the owner); refines D58.
The Kyiv registry (screenshots from a member's page) shows that the MQTT access is per node entry: the
login is the node id in hex, the password is a short string, the topic is `node/<id>`, and the entry has
"unbind" and "delete access" buttons. The owner's physical node (a Meshadventurer) came from a previous
owner who knows its keys, so its keys must be replaced.
**Decision:**
- The hub uses the claimed node's login, password and root topic (D58) but keeps **its own** node id and
  keys; it does not copy the physical node's private key. Whether the broker accepts a second client under
  the same login (the hub's client id differs) and whether the gateway id in a `ServiceEnvelope` must match
  the topic are open (§6.6) and answered by the field test (S23).
- The physical node's keys are made new **before** it appears in the mesh and before the community
  registers it. Firmware 2.7 derives the node id from the MAC, so new keys keep the id. From 2.8 the node
  id is `crc32(public key)`, so new keys change the id, and **any key change after the login was issued
  breaks it**: the keys are final before the registration. The owner flashes 2.8.1 (alpha) and backs up
  the keys (`meshtastic --export-config`, kept outside the repository).
- The Kyiv network runs on 433 MHz, and the owner chose region `EU_433` (the website's text) for the node and
  for the lab. The community's QR carries region enum 14, which the library names `UA_433`; the band is the
  same. The node took the QR first and was then set to `EU_433` (its id and key are unaffected).
- The password goes only into `.env` (`KYIV_MQTT_PASSWORD`), never into chat or the repository.
**Consequences:** nothing in the code changes. design.md §6.6 gets the two open questions above.


## D62. The first credentials received are for another broker and do not match D58; nothing is connected until the owner confirms them
**Status:** open (2026-10-05, the owner).
The owner's node was flashed with 2.8.1, its keys replaced and its id fixed (D61). The credentials that
came back are for `mqtt.wikimesh.in.ua`, not `mqtt.meshtastic.kyiv.ua`: the login looks like a person's
name and is not the node's id, the root topic is `kyiv` and not `node/<id>`, and encryption is **off**
(the node would publish decoded packets). D58 and D61 assumed the Kyiv broker, the node's id as the login and
`node/<id>` as the root.
**Decision:** the owner asks the issuers whether the access is meant for this node and hub; the node's id
is not written to the server's configuration until then. The credentials go only into `.env` (a separate
variable pair, not `KYIV_MQTT_*`), never into chat or the repository. If they are confirmed, a read-only
check shows what the broker passes under `kyiv/#`, and whether the hub's node, which expects encrypted
packets, can read decoded ones; the result goes into spikes.md and D58 is amended.
**Consequences:** no code or config changes; the Meshtastic endpoints stay unconnected in production.
**Progress (2026-10-05/06):** the login works; a text goes both ways between the owner's node (through the phone's proxy) and a stock `meshtasticd`
on a private channel of ours, with MQTT encryption **on** on both sides. A node with encryption on drops decoded envelopes, one with it off exposes
its text on the broker, so the hub's node uses it on. Channel broadcasts were lossy (about a third); **direct messages were ACKed 5 of 5** and three came
back (spikes.md S23 part 1), so D59 (`dm` only) stands. Open: the hub's ACK back to a member, the hub itself on this broker (production gets its own
node id), and the broker's long-term terms.

