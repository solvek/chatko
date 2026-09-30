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
or Meshtastic legs needs a new Briar group or a new channel PSK. Another source can replace Telegram
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
**Decision:** the core knows no network. Extensions implement capabilities (legs, membership source,
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
cut-off is done by re-creating the group's Briar leg (dissolve, create, invite the linked members). The
`/briar` help tells members to add each other nearby and to reveal their contacts in the group.
**Consequences:** the briar-headless patch needs no remove-member endpoint; `DELETE /v1/groups/{groupId}`
(dissolve) is required. Re-creating a Briar leg loses its history on members' phones and needs every
member to reach the hub directly once to accept the new invitation.

## D16. Routing is a Python script; the YAML declares what exists
Fixed rules ("mirror all legs of a group", feeds as a separate feature) are not flexible enough: the
owner wants to route between any endpoints (several Briar groups, a broadcast to one member), and later
between several hubs.
**Decision:** `chatko.yaml` stays declarative (extensions, groups, legs, sources, admins, peers).
Routing is `config/routing.py`: a pure function `route(message, ctx) → targets` that imports only the
public `chatko.routing_api`. The core ships a default router (`mirror`: all other legs of the group)
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
