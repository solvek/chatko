# chatko design

Status: **draft**, no code yet. This document describes **behaviour**. The code structure is described
in [architecture.md](architecture.md). Items marked **(verify)** must be confirmed by a spike
([spikes.md](spikes.md)) or by reading the upstream source before we rely on them.

## 1. Goal

A closed group of people keeps talking by text even when some of them have no mobile network and no
internet. A group lives in several networks at once, for example a Telegram group, a Briar private group
and a Meshtastic channel. The hub copies every message to all the other places of the same group.

One installation serves **several independent groups**.

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Installation** | One running hub with its configuration, its enabled extensions and its admins. |
| **Extension** | A plug-in that connects chatko to one network or service: `telegram`, `meshtastic`, `briar`, later others. The core knows no network by name. An extension can be added or removed without touching the core. |
| **Extension instance** | A configured copy of an extension, with its own name, e.g. `tg` (telegram), `kyiv` and `lab` (two meshtastic instances on different MQTT brokers), `briar`. |
| **Group** | An independent chat room. It has a **membership source** and one or more **legs**. |
| **Leg** | One place where the group lives, provided by an extension instance: a Telegram chat, a Briar private group, a Meshtastic channel. |
| **Membership source** | What decides who is a member of a group. v1: a Telegram group (the `telegram` extension). Later it could be a list in the config, a web invitation, etc. |
| **Member** | A person known to the installation, with an internal id. A member has a **nick** and **identities**. Nicks and identities are installation-wide: they are the same in every group. |
| **Identity** | A proven account of a member in some network: `telegram:123456789`, `meshtastic:!a1b2c3d4`, `briar:<author id>`. A member can have any number of identities, with at most one Briar identity. |
| **Nick** | A short Latin name (2–6 characters) that shows who wrote a message on every leg, e.g. `NatAda`. |
| **Control surface** | Where a member manages their account and gets system messages. v1: the private chat with the Telegram bot. Later: a web UI, etc. |
| **Feed** | A one-way copy of a channel (e.g. the public `LongFast` chat of the Kyiv mesh) into the control surface of selected members. |

### 2.1 Authentication is replaceable

- Telegram is the only **authentication** method in v1. A Telegram user is recognized as a member,
  and gets a member record, when they are in a group whose membership source is a Telegram chat.
- The member record does not depend on Telegram: the internal id owns the nick and all identities.
  A future authentication extension (web login, passkey, invitation code) attaches a new identity to
  the same member. Such an extension can then become a membership source for new groups, or replace
  Telegram entirely.
- Installation admins are listed in the config by identity (`telegram:123456789`), not by a Telegram
  field.

## 3. Reachability

The hub runs on a server with internet (a Linux cloud VM; during development, the owner's computer).
With a virtual Meshtastic node (§6.1), a member without internet reaches it only through someone else
who has internet:

```
 member's radio ─LoRa─► … ─LoRa─► gateway node ─internet─► MQTT broker ─► virtual node (hub)
                                  (MQTT uplink + downlink)
```

| Leg | What it gives | Works with the hub when |
|---|---|---|
| Telegram | convenient, everybody has it | the member has internet and Telegram is not blocked |
| Meshtastic | works with **no internet and no mobile network** on the member's side | a suitable gateway node is online (§6.2), or the hub has a physical node within radio range |
| Briar | independent of Telegram, runs over Tor | the member is online at least briefly, **or** another group member who synced with the hub meets them over Bluetooth/Wi-Fi (§7.3) |

The hub is a single point of failure. Deployment is one `docker compose`, and the configuration and
state are backed up daily, so the hub can be moved to another server within an hour.

## 4. Components

```
                        ┌──────────────────────────── server ──────────────────────────────┐
                        │ chatko hub (Python, asyncio)                                      │
 Telegram ◄────────────►│  ├─ core: members · groups · router · outbox · commands · config  │
                        │  └─ extensions                                                    │
                        │      ├─ telegram   (legs, membership source, control surface)     │
                        │      ├─ meshtastic ─ TCP :4403 ─► meshtasticd "kyiv" (no radio) ──┼─► MQTT broker
                        │      │             ─ TCP/USB/BLE ─► a physical node (optional) ───┼─► LoRa
                        │      └─ briar      ─ REST + WS ─► briar-headless ─────────────────┼─► Tor
                        │ config/chatko.yaml  ◄── edited by the admin                       │
                        │ data/  (SQLite, Briar and meshtasticd state)                      │
                        └───────────────────────────────────────────────────────────────────┘
```

| Component | What | Why |
|---|---|---|
| chatko hub | Core plus extensions, one Python process | [architecture.md](architecture.md) |
| `meshtasticd` | The official Meshtastic Linux firmware in Docker, simulated radio (no LoRa hardware), MQTT client on. Used when the hub has no physical node of its own (§6.1) | A complete node: NodeInfo, PKI, channel crypto, retries. We don't write our own virtual node. One container per virtual node (one MQTT broker each). |
| `briar-headless` | The official Briar peer with a REST API, plus our private-group API (§7.4) | The only supported way to talk to Briar from a program |

### 4.1 Platforms

- The hub is pure Python (3.12+) and runs on Linux, macOS and Windows, on x86-64 and ARM64.
- `meshtasticd` runs in Docker (native on Linux; Docker Desktop on macOS and Windows).
- `briar-headless` is a JVM application with Tor inside. Upstream builds it for Linux (x86-64,
  aarch64, armhf), Windows and macOS.
- The reference deployment is `docker compose` on a Linux server (x86-64 or ARM64). A development
  setup runs everything locally, including a local MQTT broker (§12).

## 5. Telegram extension

The `telegram` extension provides three things: **legs** (Telegram groups), a **membership source** and
a **control surface** (the private chat with the bot). Each of them can be switched off, or provided by
another extension instead.

### 5.1 Which groups the bot serves

- The bot serves only the Telegram chats that the config uses as legs or membership sources.
- When the bot is added to any other group:
  - if an installation admin added it, the bot posts nothing in the group and sends the admin the
    chat id to put into the config;
  - otherwise it leaves the group at once (`leaveChat`) and notifies the admins.
- The bot must see all messages: disable privacy mode in BotFather (`/setprivacy` → Disable) **and**
  make the bot a group admin with no extra rights. Admin rights are also needed to receive
  `chat_member` updates (joins and leaves).

### 5.2 Control surface

Only for members and feed users. Everyone else gets "You are not a member of any chatko group".
Commands are defined by the core and by the extensions ([architecture.md](architecture.md)). The control surface only renders them:
as text commands and, in Telegram, as buttons, so that nobody has to type.

| Command | Defined by | What it does |
|---|---|---|
| `/start`, `/help` | core | Short help, list of my groups |
| `/me` | core | My nick and identities, with the status of each |
| `/nick [NewNick]` | core | Show or change my nick |
| `/mesh` | meshtastic | Link a node (§6.3) and get the QR codes/URLs of my groups' Meshtastic channels |
| `/mesh remove !a1b2c3d4` | meshtastic | Unlink a node |
| `/briar [briar://…]` | briar | Link my Briar account, or replace it, and get invited to my groups' Briar mirrors (§7.2) |
| `/briar remove` | briar | Unlink my Briar account |

## 6. Meshtastic extension

### 6.1 Instances, nodes and channels

Each instance of the extension drives **one Meshtastic node**, the hub's node. The extension talks to it
through the official `meshtastic` Python library, which has the same API for every connection type. So
the hub's node can be either of these:

| Hub's node | Connection | How it reaches the mesh | When to use |
|---|---|---|---|
| **Virtual**: a `meshtasticd` container with a simulated radio | TCP to the container | only through MQTT and someone's gateway (§6.2) | the hub runs in the cloud, far from the mesh; no hardware needed |
| **Physical**: a real radio | USB serial or BLE on the hub's machine, or TCP to a Wi-Fi node (e.g. at home, through a VPN) | directly over LoRa. With MQTT enabled on it, it is also a gateway itself | the hub (or a Wi-Fi node the hub can reach) is within radio range of the mesh |

This is how mr-tbot/mesh-api works too: it always drives a physical node over USB, TCP or BLE, and
that node does the radio work. chatko supports both kinds.

- Each node has its own node id, key pair and long/short name. A virtual node also has **one MQTT
  connection** (host, port, TLS, user, password, root topic). The broker is fully configurable: the
  Kyiv community broker, our own Mosquitto, or any other.
- A node has up to 8 channels. Channel 0 is usually the public primary channel of the mesh (e.g.
  `LongFast`), used for feeds. The other channels are private group channels with their own PSK.
- A group may have several Meshtastic legs, even on different instances (brokers).
- A private channel must use the same modem preset as the primary channel, because secondary channels
  share its LoRa settings.

### 6.2 How a message reaches a radio

A physical hub node transmits over LoRa itself. A virtual node talks only to the MQTT broker, and its
packets get onto the air only through a **gateway**: a radio node **with its own internet connection**
and MQTT enabled. A radio node without internet is just a relay, even if it is ours. Once a packet is
on the air, any node, ours or not, can relay it further.

Each Meshtastic leg has a delivery mode. **Both modes are first-class.** A group often has only one or
two radio nodes, and an always-online gateway of our own is not guaranteed, so `dm` is the default.

| Mode | How | Needs (virtual node) | Airtime |
|---|---|---|---|
| `dm` (default) | a PKI direct message from the hub's node to every linked node of every group member. Members send to the group by a direct message to the hub's node | any gateway that forwards PKI direct messages over MQTT **(verify for the Kyiv mesh: broker policy and gateway firmware)**. The hub's node and the member's node must know each other's public keys, which travel in NodeInfo on the primary channel **(verify)** | 1 packet per recipient node |
| `channel` | one broadcast on the group's private channel | at least one gateway **that has this channel** (name + PSK) with uplink and downlink. Other people's gateways don't know our channel, so this means our own internet-connected node(s), or a trusted gateway operator who adds our channel | 1 packet per message |

- With a physical hub node, both modes work within its radio range without any gateway.
- Both modes can be used in one group, e.g. `channel` for the area covered by our gateway and `dm`
  elsewhere. The hub doesn't send a member the same message through both modes: a member whose node
  was heard on the group channel recently gets it there, and everyone else by `dm`.
- `dm` messages ask for an acknowledgement. Without an ACK the hub retries later, when the node is heard
  again.
- Relaying routers of the mesh forward packets of unknown channels without decrypting them (the default
  rebroadcast mode is `ALL`) **(verify)**, so a private-channel packet still travels over foreign relays.
- Research idea, not planned: publish private-channel packets under the topic of a channel that foreign
  gateways do downlink (e.g. `LongFast`), so they carry our encrypted packets too. It depends on
  firmware behaviour and broker policy, and could be seen as abusing the public network.

### 6.3 Identifying the sender

- Anyone who has the channel PSK can post, so the bot hands out the PSK (as a Meshtastic channel URL
  and a QR code) only to group members through the control surface.
- A member links a node via `/mesh`: the bot shows a code (e.g. `K7Q2`). The member sends it as a
  **direct message** from the radio to the virtual node, and the hub adds the identity
  `meshtastic:!a1b2c3d4` to the member. After that, messages from that node carry the member's nick.
- A member may link several nodes (e.g. a handheld and a home node) and unlink any of them. A node
  belongs to at most one member.
- In `dm` mode, a member's direct message to the virtual node goes to their group. If the member is in
  several groups with `dm` legs, the message goes to their default group, which they choose in the
  control surface (a `#group` prefix overrides it).
- Messages from nodes nobody has linked are relayed with the node's own short name and a `~` mark:
  `~BC1: text`.
- Removing a member from the group does not remove them from the private channel. That needs a new PSK
  on all radios. The bot reminds the admin about it.

### 6.4 Size limits and airtime

- One text packet carries ≤ 200 UTF-8 bytes. Cyrillic letters take 2 bytes each, so a packet holds
  about 90 Cyrillic characters.
- Longer text is split on spaces into `NatAda (1/3): …`, at most 3 parts. Anything beyond that is
  truncated with `…`, and the source leg is told about it (in Telegram, a ✂️ reaction).
- Non-text content goes out as a placeholder: `NatAda: [photo] caption`.
- The hub sends no faster than one packet every few seconds per node (configurable), to be a good
  neighbour on a busy mesh.

### 6.5 Feeds

- The config lists feeds: a source (instance + channel) and the members who receive it.
- Every text message on that channel goes to each listed member's control surface as
  `[BC1] Base Camp: text`. In Telegram, the member must have pressed Start in the bot once.
- Feeds are one-way in v1.

## 7. Briar extension

### 7.1 The hub's Briar account

The extension drives `briar-headless`, a full Briar account (e.g. nickname `chatko`) that is online all
the time over Tor. It **creates** each group's Briar leg as a Briar private group, so the hub is the
creator. In Briar only the creator can invite people, so the group stays closed and the hub controls
who is invited.

The hub posts messages from the other legs into the Briar group as `NatAda: text` (no size limit).
Members' own posts reach the other Briar members natively. The hub copies them to the other legs and
never echoes them back into Briar.

### 7.2 Linking a Briar account

A member has at most one Briar identity, used for all of their groups.

1. In the control surface: `/briar` → the hub shows its `briar://` link and asks for the member's link.
2. The member adds the hub's link in Briar and sends their own link.
3. The hub adds the pending contact. When Briar reports `ContactAddedEvent`, the hub adds the identity
   `briar:<author id>` to the member and invites the contact to the Briar leg of every group they are
   in.
4. When the member later joins another group with a Briar leg, the hub invites the same contact there.

**Replacing** (`/briar` with a new link, e.g. after changing phones): the new contact goes through
steps 1–3, then the old one is unlinked.

**Unlinking** (`/briar remove`): the hub deletes the contact and removes the identity. It also tries to
remove the account from the Briar groups. Briar may not support removing a member from a private group
**(verify)**. In that case the old account stays in the groups it joined and can still read them
through other members. The hub relays its posts as unlinked (`~BriarNick: text`), or ignores them if
the admin configures that. To really cut it off, the admin re-creates the group. This limitation is
documented in the README.

### 7.3 Relay through other members

Private-group messages are signed by their author and sync between **any** two group members that meet
(Bluetooth or the same Wi-Fi). A message can therefore travel hub → C → B → A while A never goes
online **(verify, spike S1)**. Conditions:

- A and B are Briar contacts of each other (the easiest way is to add each other once in person with
  "Add contact nearby"), **and** they reveal that relationship in the group ("Reveal contacts").
  Otherwise they sync only through the creator. **(verify: is it enough for one of the two to reveal?)**
- Not everybody has to be a contact of everybody. Contacts between people who often meet in person are
  enough.
- Briar runs in the background with Bluetooth on (battery optimization disabled).

Private direct messages cannot be relayed this way: a two-person conversation syncs only between its
two participants. That is one reason v1 has group messages only.

### 7.4 What briar-headless lacks

Its REST API covers contacts, private messages, blog posts and listing/creating forums. It has **no
private-group API**. We therefore patch `briar-headless` (Kotlin) with a thin layer over Briar's
existing `PrivateGroupManager` and `GroupInvitationManager`:

| Endpoint | Purpose |
|---|---|
| `GET /v1/groups` | list private groups |
| `POST /v1/groups` | create a private group `{ name }` |
| `POST /v1/groups/{groupId}/invitations` | invite a contact `{ contactId, text }` |
| `DELETE /v1/groups/{groupId}` | dissolve the group (used to re-create it) |
| `GET /v1/groups/{groupId}/messages` | list group messages |
| `POST /v1/groups/{groupId}/messages` | post `{ text }` |
| WS `GroupMessageAddedEvent` | a new message in a group |
| WS invitation response events | the member accepted or declined |

The patch lives in our fork (with tests, in upstream style) and is offered upstream to Briar.

## 8. Nicks

- A nick is 2–6 characters `[A-Za-z0-9]`, unique within the installation (case-insensitive).
- It is generated automatically from the display name that the authenticating identity provides:
  transliterate it (Ukrainian by the official KMU-2010 table, Russian by a similar one), then take the
  first 3 letters of the first name and the first 3 letters of the last name, each capitalized:
  *Наталія Адамчук* → *Nataliia Adamchuk* → `NatAda`. With a single name, the first 6 letters. On a
  collision the last character becomes a digit: `NatAd2`.
- The member can change it with `/nick`, and an installation admin can change anyone's nick.
- The same nick is used on every leg, so people learn one name per person.

## 9. Message format

| Direction | Text |
|---|---|
| Into Meshtastic or Briar, from a member | `NatAda: text` |
| Into Telegram, posted by the bot | `NatAda: text` |
| From a node nobody has linked | `~BC1: text` |
| From a Briar author nobody has linked | `~BriarNick: text` |
| Feed, into the control surface | `[BC1] Base Camp: text` |

Replies, edits and deletions are not mirrored in v1. A reply is sent as plain text.

## 10. Routing rules

- A message from one leg of a group goes to **all other legs of the same group**, never back to its own
  leg.
- Each extension drops the hub's own posts (the bot's messages, the virtual node's packets, the hub's
  Briar posts), so they never come back as incoming.
- Incoming messages are de-duplicated by the id the extension provides: Meshtastic packets often arrive
  once per gateway.
- Only members may post from Telegram. Posts from other networks are accepted from anyone who has
  access to the leg, marked with `~` if the author is not linked (§9).
- Each `(message, leg)` delivery is a row in the outbox. A failed delivery is retried with backoff and
  survives a restart.

## 11. Configuration

`config/chatko.yaml` is edited by the admin and reloaded on change. If the new file is invalid, the hub
keeps the previous configuration and reports the error to the admins. Secrets come from environment
variables (`${VAR}` in YAML, values in `.env`), never from the file itself. See
[`config.example.yaml`](../config.example.yaml).

The hub never writes this file. What members change themselves (nicks, identities, default group) lives
in SQLite.

## 12. Development and deployment

- **Development (now):** everything on the owner's computer. `docker compose` with a local Mosquitto,
  the hub's `meshtasticd`, and a second `meshtasticd` that plays the member's radio, so both channel and
  `dm` delivery can be tested without hardware. When a hardware node is available, it is tested both as a
  member's radio and as the hub's physical node. `briar-headless` is built locally. A read-only
  connection to the Kyiv broker is used to receive `LongFast`.
- **Production:** a Linux server (cloud VM, x86-64 or ARM64), the same compose file. Inbound ports:
  SSH only. Telegram long polling, MQTT and Tor are all outbound. The `meshtasticd` TCP API (4403) has
  no authentication, so it stays inside the Docker network.
- Volumes: `config/` (chatko.yaml, meshtasticd configs) and `data/` (SQLite, Briar and meshtasticd
  state, including node keys). Secrets live in `.env`. `config/` and `data/` are backed up daily.
- Hosting candidate: Oracle Cloud Always Free (Ampere A1, ARM64). Oracle reclaims Always Free instances
  that look idle for 7 days. A Pay As You Go account keeps the free limits and is not reclaimed.

## 13. Plan

The phases are split into working sessions in [roadmap.md](roadmap.md).

| Phase | What | Done when |
|---|---|---|
| 0. Spikes | S1 Briar relay with 3 phones. S2 local Meshtastic lab. S3 briar-headless build and API. See [spikes.md](spikes.md) | Every (verify) that affects v1 has an answer |
| 1. Core | members, nicks, groups, router, outbox, commands, config, SQLite; extension API and the contract test suite; a fake extension | the core is fully tested with fake extensions |
| 2. Telegram | legs, membership source, control surface, access control | two Telegram groups work independently; foreign groups are left |
| 3. Meshtastic | legs (`channel`, `dm`), node linking, splitting, feeds | radio ⇄ Telegram in the local lab, then on the Kyiv mesh |
| 4. Briar | private-group API patch, Briar leg, linking | Briar group ⇄ Telegram group |
| 5. Later | direct messages, web UI (as a control surface and an authentication extension), SMS, MeshCore, monitoring | — |

## 14. Other networks worth adding later

1. **Web UI (PWA)**: a second control surface and authentication method, and the main fallback if
   Telegram fails.
2. **SMS**: for when mobile data is down but voice and SMS still work. Outbound through a Ukrainian SMS
   API; two-way through an Android phone acting as a gateway. It costs money per message.
3. **MeshCore**: a second LoRa mesh growing in Ukraine. mr-tbot/mesh-api has code for it.
4. **Email / Delta Chat**, **Signal** (signal-cli), **Reticulum/LXMF** (Sideband) for technical members.
