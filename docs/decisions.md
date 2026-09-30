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
