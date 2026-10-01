# chatko design

Status: **draft**; phase 1 (the core) is being written. This document describes **behaviour**. The
code structure is described in [architecture.md](architecture.md). Items marked **(verify)** must be
confirmed by a spike ([spikes.md](spikes.md)) or by reading the upstream source before we rely on
them. Phase 0 answered every (verify) that v1 relies on (session S07); the questions still open
concern only the Kyiv community's broker and gateways, which v1 does not use, and are listed in §6.6.

## 1. Goal

A closed group of people keeps talking by text even when some of them have no mobile network and no
internet. A group lives in several networks at once, for example a Telegram group, a Briar private group
and a Meshtastic channel. By default the hub copies every message to all the other places of the same
group. Where a message goes, and how its author is signed, is decided by a **routing script** that the
admin writes (§9).

The hub only relays. It does not manage people: who is in a Telegram group, a Briar group or a
Meshtastic channel is decided in those networks and in the config (D22).

One installation serves **several independent groups**. Several installations can later work together,
e.g. a hub in the cloud and a hub at home without internet (§9.6).

v1 is one hub in the cloud that keeps a Briar group, a Telegram group and Meshtastic (a channel, or
DMs to several nodes) in sync (D20).

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Installation** | One running hub with its configuration and its enabled extensions. |
| **Extension** | A plug-in that connects chatko to one network or service: `telegram`, `meshtastic`, `briar`, later others. The core knows no network by name. An extension can be added or removed without touching the core. |
| **Extension instance** | A configured copy of an extension, with its own name, e.g. `tg` (telegram), `kyiv` and `lab` (two meshtastic instances on different MQTT brokers), `briar`. |
| **Endpoint** | One place where an extension instance reads and posts messages: a Telegram chat, a Briar private group, a Meshtastic channel, a set of Meshtastic nodes reached by DM. |
| **Group** | An independent chat room: a named set of **legs**. |
| **Leg** | An endpoint that belongs to a group: one place where the group lives. |
| **Source** | An endpoint that belongs to no group but is still read, e.g. the public `LongFast` channel. Its messages go only where the routing script sends them. |
| **Account** | An author in some network: `telegram:123456789`, `meshtastic:!a1b2c3d4`, `briar:<author id>`, with the display name the network gives. |
| **Person** | Optional, in the config: a label (e.g. `NatAda`) and the accounts of one human, so that they are signed the same way in every network (§8). |
| **Author label** | The name in front of a relayed message: `NatAda: text` (§8). |
| **Routing script** | Admin-written Python (`config/routing.py`) that decides, for every incoming message, where it goes and how its author is labelled (§9). The core ships defaults. |
| **Feed** | A one-way copy of a source into some endpoint, e.g. `LongFast` into the owner's private chat with the bot. Just a routing rule (§6.5). |
| **Admin notices** | Messages from the hub to the admin (errors, a foreign Telegram group) posted into an endpoint named in the config. |
| **Peer hub** | Another chatko installation whose accounts this hub knows, so it can recognize the messages the peer relays (§9.6, future). |

## 3. Reachability

The hub runs on a server with internet (a Linux cloud VM; during development, the owner's computer).
With a virtual Meshtastic node (§6.1), a person without internet reaches it only through someone else
who has internet:

```
 person's radio ─LoRa─► … ─LoRa─► gateway node ─internet─► MQTT broker ─► virtual node (hub)
                                  (MQTT uplink + downlink)
```

| Network | What it gives | Works with the hub when |
|---|---|---|
| Telegram | convenient, everybody has it | the person has internet and Telegram is not blocked |
| Meshtastic | works with **no internet and no mobile network** on the person's side | a suitable gateway node is online (§6.2), or (later) the hub has a physical node within radio range |
| Briar | independent of Telegram, runs over Tor and over a local Wi-Fi network | the person is online at least briefly, **or** another group member who synced with the hub meets them over Bluetooth/Wi-Fi (§7.3), **or** (later) the phone is on the same Wi-Fi network as a hub (§9.6) |

The hub is a single point of failure. Deployment is one `docker compose`, and the configuration and
state are backed up daily, so the hub can be moved to another server within an hour.

## 4. Components

```
                        ┌──────────────────────────── server ──────────────────────────────┐
                        │ chatko hub (Python, asyncio)                                      │
 Telegram ◄────────────►│  ├─ core: endpoints · routing · outbox · labels · config          │
                        │  └─ extensions                                                    │
                        │      ├─ telegram   ─ Bot API                                      │
                        │      ├─ meshtastic ─ TCP :4403 ─► meshtasticd "kyiv" (no radio)   │
                        │      │                            └─ MQTT ─► mosquitto ◄──────────┼── :8883 ── our gateway ─► LoRa
                        │      │             ─ TCP/USB/BLE ─► a physical node (later) ──────┼─► LoRa
                        │      └─ briar      ─ REST + WS ─► briar-headless ─────────────────┼─► Tor
                        │ config/chatko.yaml, config/routing.py  ◄── edited by the admin    │
                        │ data/  (SQLite, Briar and meshtasticd state)                      │
                        └───────────────────────────────────────────────────────────────────┘
```

| Component | What | Why |
|---|---|---|
| chatko hub | Core plus extensions, one Python process | [architecture.md](architecture.md) |
| `meshtasticd` | The official Meshtastic Linux firmware in Docker, simulated radio (no LoRa hardware), MQTT client on (§6.1) | A complete node: NodeInfo, PKI, channel crypto, retries. We don't write our own virtual node. One container per virtual node (one MQTT broker each). |
| Mosquitto | Our MQTT broker, in the same compose file; the hub's nodes and our gateways connect to it (§6.2) | The Kyiv broker gives logins only to claimed physical nodes (D27, D30). With a broker of our own, the gateways that know our private channels are ours too |
| `briar-headless` | The official Briar peer with a REST API, plus our private-group API (§7.4) | The only supported way to talk to Briar from a program |
| `briarctl` | A command-line tool for the admin to manage the hub's Briar account: contacts, groups, invitations (§7.5) | Kept out of the relay, so chatko itself never manages people |

### 4.1 Platforms

- The hub is pure Python (3.12+) and runs on Linux, macOS and Windows, on x86-64 and ARM64.
- `meshtasticd` runs in Docker (native on Linux; Docker Desktop on macOS and Windows).
- `briar-headless` is a JVM application with Tor inside. Upstream builds it for Linux (x86-64,
  aarch64, armhf), Windows and macOS; it builds with JDK 17 and runs on a Java 17 runtime. We build
  the Linux jars of our fork (upstream plus the patch of §7.4) in Docker and run them in a JRE image
  (spike S3).
- The reference deployment is `docker compose` on a Linux server (x86-64 or ARM64). A development
  setup runs everything locally, including a local MQTT broker (§11).

## 5. Telegram extension

- Endpoints are Telegram chats by id: groups, and private chats with the bot (e.g. for a feed or for
  admin notices; the person must have pressed Start once).
- The bot serves only the chats listed in the config. When it is added to any other group, it leaves at
  once (`leaveChat`) and sends an admin notice with the chat id, so the admin can add the chat to the
  config and add the bot again. Private messages from chats not in the config are ignored.
- Who may write in a Telegram group is decided by its Telegram admins.
- The bot must see all messages: disable privacy mode in BotFather (`/setprivacy` → Disable) **and**
  make the bot a group admin with no extra rights.
- The bot has no commands in v1.

## 6. Meshtastic extension

### 6.1 Instances, nodes and channels

Each instance of the extension drives **one Meshtastic node**, the hub's node. The extension talks to it
through the official `meshtastic` Python library, which has the same API for every connection type. So
the hub's node can be either of these:

| Hub's node | Connection | How it reaches the mesh | When to use |
|---|---|---|---|
| **Virtual**: a `meshtasticd` container with a simulated radio | TCP to the container | only through MQTT and someone's gateway (§6.2) | the hub runs in the cloud, far from the mesh; no hardware needed |
| **Physical** (later, not in v1): a real radio | TCP to a node on the local Wi-Fi, BLE from the hub's machine, or USB serial | directly over LoRa. With MQTT enabled on it (and internet), it is also a gateway itself | the hub (or a node the hub can reach) is within radio range of the mesh, e.g. a home hub on a Raspberry Pi (§9.6) |

This is how mr-tbot/mesh-api works too: it always drives a physical node over USB, TCP or BLE, and
that node does the radio work. v1 implements only the virtual node, for a hub in the cloud (D20). The
connection setting and the adapter port leave room for a physical node, which a home hub needs.

A virtual node without `meshtasticd` (the hub publishing encrypted packets to MQTT itself) is possible
for `channel` delivery, but `dm` delivery would need our own PKI, NodeInfo, ACK and retry code. We keep
`meshtasticd` (D3, D19).

- Each node has its own node id, key pair and long/short name. A virtual node also has **one MQTT
  connection** (host, port, TLS, user, password, root topic). The broker is fully configurable: the
  Kyiv community broker, our own Mosquitto, or any other.
- The hub node's identity: the node id is fixed by the MAC address in `meshtasticd`'s YAML (in
  `config/`). The key pair is made by the node when a region is first set and kept in its volume,
  unless the config sets `private_key` (a secret in `.env`); then the hub provisions it and the key
  survives a lost volume or a move to another server (D26). This matters because members' radios pin
  the hub's public key: with a new key, direct messages between the hub and every radio that knew the
  old one fail until that radio is given the new key (spike S2). The hub logs its public key at start.
- The node's channels and their PSKs are set in the config, and the hub provisions the node from it. The
  admin gives the PSK (a channel URL or QR code from the Meshtastic app) to the people who should have it.
  Removing someone from a channel means a new PSK on all radios.
- Provisioning (D25) goes through the node's admin API over the same connection the hub uses for
  messages: names, region, `ignore_mqtt` off, the private key if configured, the MQTT client, each
  channel with uplink and downlink on, and the configured `contacts` (below). It writes only what
  differs from the node's current settings, sends one admin message at a time and waits for the node's
  response to each (the node drops the oldest when more than 4 wait, D26), and reconnects when the node
  reboots (a commit always reboots it; a `meshtasticd` reboot ends the process and its container
  restarts it). `meshtasticd`'s own YAML sets only the simulated radio, the MAC address (which fixes
  the node id) and logging.
- A channel is matched over MQTT by its **name** (the topic `<root>/2/e/<name>/<gateway>` and the
  envelope carry it; the packet carries only a hash), then decrypted with its PSK. A private channel must
  have the same name and PSK on the hub's node and on every radio.
- `meshtasticd` serves one API client at a time: a new connection drops the previous one. The hub is the
  only client of its node; the admin does not connect the Meshtastic app or CLI to it while the hub runs.
- A node has up to 8 channels. Channel 0 is usually the primary channel of the mesh (e.g. `LongFast`;
  on the Kyiv mesh it has a non-default PSK), used as a source. The other channels are private group channels with their own PSK.
- A group may have several Meshtastic legs, even on different instances (brokers).
- A private channel must use the same modem preset as the primary channel, because secondary channels
  share its LoRa settings.

### 6.2 Endpoints and how a message reaches a radio

A virtual node talks only to the MQTT broker, and its packets get onto the air only through a
**gateway**: a radio node **with its own internet connection** and MQTT enabled. A radio node without
internet is just a relay, even if it is ours. Once a packet is on the air, any node, ours or not, can
relay it further.

A Meshtastic endpoint is one of two kinds. **Both are first-class.** A group often has only one or two
radio nodes, and an always-online gateway of our own is not guaranteed.

| Endpoint | Out | In | Needs (virtual node) | Airtime |
|---|---|---|---|---|
| `channel` | one broadcast on the private channel | any text on that channel | at least one gateway **that has this channel** (name + PSK) with uplink and downlink. Other people's gateways don't know our channel, so this means our own internet-connected node(s), or a trusted gateway operator who adds our channel | 1 packet per message |
| `dm` with a list of node ids | a PKI direct message to every listed node | a direct message to the hub's node from a listed node | any gateway on the hub's broker that downlinks on the primary channel: it needs no private channel. In v1 that is our own gateway (below, D30); whether the Kyiv gateways would do it is open (§6.6). The hub's node and the person's node must know each other's public keys (below) | 1 packet per listed node, plus an ACK and its ACK |

- A node listed in several `dm` endpoints: its direct messages go to the first of them in the config;
  the routing script can send them elsewhere. Direct messages from nodes not listed anywhere are logged
  and dropped.
- **Keys for `dm`.** A text direct message needs the public keys on both sides: the hub's node refuses
  to send without the person's key, and radios drop old channel-encrypted direct texts. Keys travel in
  NodeInfo on the primary channel (each node broadcasts it 30 s after boot and every 3 h), and a node
  that cannot decrypt a direct message answers with a NAK after which the sender's node sends it its
  NodeInfo at once. Asking a node for its NodeInfo works only now and then: a node answers at most once
  per 10 minutes, and once per 12 hours to the same asker (spike S2). So the config may give the
  public key of each `dm` node (`contacts`), which the hub adds to its node as a favorite contact. A
  listed node whose key the hub's node learned by itself is made a favorite too: favorites are saved at
  once (other learned keys are written at most once a minute and can be lost on a restart) and are
  never evicted from the node database (which fills up with the nodes of a busy primary channel).
- **Key pinning.** A node keeps the first key it learned for a node and ignores NodeInfo with another
  key. When a person resets their radio, the hub's node keeps the old key: the radio answers the hub's
  direct messages with NAK `NO_CHANNEL` (with `PKI_UNKNOWN_PUBKEY` while it does not know the hub yet),
  and its NodeInfo with the new key reaches the hub but is dropped by the node. The hub posts an admin
  notice; the admin puts the new key into `contacts`, which replaces the pinned one. The same happens
  on every radio if the hub's key changes, hence `private_key` (§6.1).
- **ACKs.** `dm` messages ask for an acknowledgement, and ACKs come back through MQTT. Only an ACK from
  the listed node means delivered. The hub's own node also reports an "implicit" ACK as soon as the
  broker echoes the packet back, which only means the broker has it; a text that got not even that was
  dropped by the node (e.g. sent too soon, §6.3). Without the real ACK the node publishes the packet
  twice more (about 7.5 s apart) and gives up after about 23 s; the hub then retries later, when the
  node is heard again (D26).
- ACKs and NodeInfo are channel packets on the **primary** channel, while direct messages go under the
  MQTT topic `<root>/2/e/PKI/…`. So `dm` needs uplink and downlink on the primary channel of the hub's
  node, and one direct message is three packets on MQTT: the message, the ACK, and a short ACK of the
  ACK.
- On a real mesh, a gateway uploads packets it can decode (channel texts, ACKs, NodeInfo) from a radio
  to a broker on a public address only if that radio has **"OK to MQTT"** on. It is off by default, so
  members turn it on, like "Ignore MQTT" off below. A gateway downlinks a direct message only if it
  knows both the hub's node and the person's node, and it learns the hub's node only through MQTT
  downlink on the primary channel. **Kyiv (spike S2, part 3):** the primary channel there is `LongFast`
  on `EU_433` with its own PSK (from the QR code at <https://meshtastic.kyiv.ua/join>, the same for all
  members), so the hub's primary channel must use that PSK. The broker `mqtt.meshtastic.kyiv.ua`
  gives a login and the root topic `node/<node id>` per **claimed physical node** that the registry has
  seen; a virtual node cannot get them (D27). What that broker does with the packets is open (§6.6).
- **The v1 path: our own broker and our own gateway (D30).** The hub's node uses our own Mosquitto,
  which runs next to the hub. At least one **gateway of our own** connects to the same broker over the
  internet: a physical `EU_433` radio with internet (an ESP32 node on Wi-Fi; a node whose phone app
  relays MQTT for it may do as well, untested), with the same root topic, the mesh's primary channel and the group's private
  channels, uplink and downlink on, "Ignore MQTT" off. It is the only way between the hub and the air
  until the Kyiv broker is open to the hub, so it carries both `channel` and `dm` endpoints, and it
  should stand where the group's radios, or relays that reach them, can hear it. Its uplink of other
  radios' packets follows "OK to MQTT" (above), because the broker has a public address. Such a node
  can later be claimed for the Kyiv broker (D27).
- Both kinds can be used in one group. Then a person with a node on both gets the message twice, unless
  the routing script skips `dm` for nodes recently heard on the channel (`last_heard`, §9.5).
- With a physical hub node (later), both kinds work within its radio range without any gateway.
- **"Ignore MQTT" must be off** on the person's radio, and on every relay between the gateway and it.
  A packet from the hub's virtual node is marked "via MQTT", and the mark travels in the LoRa header after
  a gateway downlinks it. A node with `ignore_mqtt` on drops such a packet before it would show or relay
  it (spike S2, firmware 2.7.26). The firmware turns `ignore_mqtt` on when a region with a duty-cycle
  limit is set for the first time: `EU_868`, `EU_433` (the Kyiv mesh), `UA_433` and `UA_868` among
  them (`AdminModule.cpp`, `RadioInterface.cpp`, read in S07), so many radios have it on. Setup
  instructions for members say to turn it off. How common it is on the Kyiv relays is open (§6.6);
  the closer our gateway stands to the group's radios, the fewer relays matter.
- Relays forward packets of channels they do not know without decrypting them, so a private-channel
  packet still travels over foreign relays. This holds for the default rebroadcast mode `ALL` and for
  `CORE_PORTNUMS_ONLY` (the default of the `ROUTER` role). Nodes set to `LOCAL_ONLY` or `KNOWN_ONLY`
  (and `CLIENT_HIDDEN` nodes by default) drop them, and relay a direct message only if they know its
  sender or receiver (firmware 2.7.26 source, spike S2).
- Research idea, not planned: publish private-channel packets under the topic of a channel that foreign
  gateways do downlink (e.g. `LongFast`), so they carry our encrypted packets too. It depends on
  firmware behaviour and broker policy, and could be seen as abusing the public network.

### 6.3 Size limits and airtime

- One text packet carries ≤ 200 UTF-8 bytes. Cyrillic letters take 2 bytes each, so a packet holds
  about 90 Cyrillic characters.
- Longer text is split on spaces into `NatAda (1/3): …`, at most 3 parts. Anything beyond that is
  truncated with `…`, and the source endpoint is told about it where the network allows (in Telegram, a
  ✂️ reaction).
- Non-text content goes out as a placeholder: `NatAda: [photo] caption`.
- The hub sends no faster than one packet every few seconds per node (configurable), to be a good
  neighbour on a busy mesh. The interval is per hub node (all its endpoints together) and at least 2 s:
  the node silently drops a text from the hub that comes sooner (spike S2).
- A direct message carries 12 bytes more than a channel packet (PKI), so it holds at most about
  220 bytes of text (a channel packet about 230); the 200-byte limit fits both.

### 6.4 Authors

A Meshtastic message carries the sender's node id and, once NodeInfo was heard, the node's long and
short name (a direct message also carries the sender's public key). The author label comes from these
(§8). Until NodeInfo is heard, only the node id (`!c4a7b002`) is known: the Python library then shows a
placeholder name `Meshtastic b002`, which is not the node's name and is not used as one.

### 6.5 Feeds

- The config declares the channel as a **source** (e.g. `longfast: { ext: kyiv, channel: LongFast }`).
- The routing script sends its messages to any endpoint, e.g. a private chat with the bot, as
  `[BC1] Base Camp: text`.
- A feed is one-way: messages in the target endpoint are routed by their own rules, not back to the
  source, unless the script says so.
- With our own broker (§6.2), the hub hears the mesh's `LongFast` only through our gateways, so the
  feed carries what they hear.

### 6.6 Open questions about the Kyiv mesh (not needed for v1)

v1 reaches the Kyiv mesh only through our own broker and gateway (§6.2, D30), so none of these block
it. They decide whether the hub can later use the Kyiv broker and the community's gateways directly
(D27). The questions were drafted for the community in session S04; the answers go into spikes.md
(S2, part 3) and here, once a physical node is claimed (roadmap S04) or during the field test (S23).

| Question | What it decides |
|---|---|
| What the broker's "enable routing" does with packets published under `node/<id>`: are they bridged to other nodes' topics, and may a client read them? | whether a hub on the Kyiv broker hears the mesh and is heard by it at all |
| The broker's policy for PKI direct messages (`…/2/e/PKI/…`) and for downlink | whether `dm` works through the community's gateways |
| The firmware versions and settings of the gateways: downlink on the primary channel, "Ignore MQTT", rebroadcast mode | whether a gateway learns the hub's node and downlinks its packets (§6.2) |
| How many relays run with "Ignore MQTT" on, and how many radios have "OK to MQTT" on | how far the hub's packets travel over the air, and whether replies come back |
| Whether the community accepts a bot node on the mesh | the hub's node is visible on `LongFast` through our gateway in v1 too (NodeInfo, ACKs), so the owner asks before the field test (S23) |

## 7. Briar extension

### 7.1 The hub's Briar account

The hub's `briar-headless` is a full Briar account (e.g. nickname `chatko`) that is online all the time
over Tor. Tor is its only transport: unlike the app, it does not sync over Wi-Fi or Bluetooth (spike
S3). Briar's Wi-Fi (LAN) transport is platform-independent code and could be switched on in
`briar-headless` with a small patch, which a future hub without internet needs (§9.6, D29). It
finds contacts by the last few LAN addresses that each side shares with its contacts, not by
discovery, so a phone and the hub connect over Wi-Fi only if one of them already knows the other's
current address, and a hub in Docker would need host networking.

Two separate programs use this account (D24):

| Program | Does | Does not |
|---|---|---|
| the chatko `briar` extension | reads and posts in the Briar groups listed in `chatko.yaml` | create groups, invite, add contacts |
| `briarctl`, a command-line tool (§7.5) | contacts, groups, invitations: what a person does by hand in the Briar app | relay messages, read `chatko.yaml` |

A Briar group syncs between its members through the **creator**, who shares it with everyone, and
between members who are contacts and reveal that (§7.3). A creator that is online all the time keeps
the group alive, so the recommended setup is: **the hub's account creates the groups** with `briarctl`.

1. **Contacts.** For each person, `briarctl contact add briar://…` adds their link, and the person adds
   the hub's link (`briarctl link`) in the Briar app ("Add contact at a distance"). A contact at a
   distance needs **both** sides to add each other's link: with only one side, nothing happens; once
   both have, the contact appears in seconds (spike S3). Such contacts stay "unverified" in Briar
   (only contacts added in person are verified), which does not affect syncing.
2. **Group.** `briarctl group create "Family"` prints the new group's id;
   `briarctl group invite <group> <contact>…` invites people. They accept in the app.
3. **Endpoint.** The admin puts the group id, as `briarctl` prints it, into `chatko.yaml` (a leg of
   a group, or a source). From then on the extension reads and posts there.

Briar ids (groups, authors) are 32 bytes. `briarctl`, `chatko.yaml` and chatko's logs write them in
URL-safe base64 without padding (43 characters of `A–Z a–z 0–9 - _`), the form the API takes in URL
paths (§7.4).

A group made by a person, who then invites the hub, works as well: `briarctl invitation accept` joins
it. Then the hub syncs only through that person's phone (§7.3).

A hub can be in any number of Briar groups, and a message can be routed into several of them.

The extension posts routed messages into a Briar group as `NatAda: text`. A post holds up to
31 744 bytes of UTF-8 text, far more than any other network sends. Posts reach the other Briar members
natively. The extension copies them to other endpoints and never echoes them back into their own group
(§9.3).

Briar cannot remove a member from a private group (spike S1, D15). To cut someone off, the admin
dissolves the group and creates a new one without them (`briarctl group dissolve`, `create`, `invite`),
then changes the endpoint in `chatko.yaml`. History on phones is lost, and every remaining member must
reach the hub once to accept the new invitation (an invitation is not relayed by other members).

### 7.2 Posts and authors

A post carries its author's Briar identity (`briar:<author id>`) and nickname. The author label comes
from these (§8).

- **In.** The extension gets other members' posts from `briar-headless`'s WebSocket. The WebSocket
  only reaches a connected client, so after every start or lost connection the extension also lists
  the messages of each configured group and submits the posts it has not handed over yet. It marks a
  post as read in Briar once the hub has stored it, and Briar's read flag is used for nothing else.
  Nothing is lost while chatko is down, and the overlap between the WebSocket and the list is removed
  by the message id (§9.1).
- **Own posts.** The hub's own posts are stored as read and marked as its own, and never submitted
  (§9.1, step 1).
- **Order.** Each post is chained to its author's previous message in the group, and Briar holds a
  post back until that message has arrived. So one author's posts arrive in the order they were
  written, even when they came through different members (§7.3).
- **Not relayed:** join notices ("Ada joined the group") and replies as such (a reply is relayed as
  plain text, §8).
- **Dissolved.** When the hub joined someone else's group and its creator dissolves it, the extension
  posts an admin notice, and posts into it are refused (§7.4). Briar also marks such a group
  dissolved when the admin removes its creator from the hub's contacts.

### 7.3 Relay through other members

Private-group messages are signed by their author and sync between **any** two group members that meet
(Bluetooth or the same Wi-Fi). A message can therefore travel hub → C → B → A while A never goes
online (confirmed by spike S1 in both directions). Conditions:

- A and B are Briar contacts of each other (the easiest way is to add each other once in person with
  "Add contact nearby"), **and** at least one of them reveals that relationship in the group ("Reveal
  contacts" in the group's ⋮ menu). Otherwise they sync the group only with the creator, even when
  they are connected to each other. The README tells people to do both.
- Not everybody has to be a contact of everybody. Contacts between people who often meet in person are
  enough.
- Every member syncs with the creator. With the hub as the creator, anyone who reaches the internet
  even briefly delivers and collects the group's messages. With a person as the creator, the hub syncs
  only through that person's phone, unless other members are also its contacts and reveal them.
- Briar runs in the background with Bluetooth on (battery optimization disabled). With the screen off,
  a message crossed between two nearby phones in 1–2 minutes.

Private direct messages cannot be relayed this way: a two-person conversation syncs only between its
two participants. That is one reason v1 has group messages only (D1).

### 7.4 The private-group API (our briar-headless patch)

`briar-headless`'s REST API covers contacts (the account's link, adding a pending contact, listing and
removing contacts), private messages, blog posts and listing/creating forums. It has **no private-group
API**, and private groups are even switched off in its core: it neither checks nor stores
private-group messages, and it does not tell its contacts that it supports them, so a phone cannot
invite it (spike S3). Everything else is in Briar's shared code (`PrivateGroupManager`,
`GroupInvitationManager` and their factories), which the Android app calls through thin
controllers. The patch (D29) switches private groups on and adds a thin REST layer that makes the same
calls as the app. Briar sets the private-group clients up for an existing account at its next start
and tells its contacts, so an existing account should need no migration (read in the source; S24
checks it with the lab account).

| Endpoint | What it does | Used by |
|---|---|---|
| `GET /v1/groups` | the account's private groups: `id`, `name`, `creator` (author), `ourGroup` (the hub is the creator), `dissolved` | both |
| `POST /v1/groups` `{name}` | create a group with the hub as its creator; returns the group | `briarctl` |
| `DELETE /v1/groups/{groupId}` | the creator dissolves the group, a member leaves it; either way it disappears from the hub with its history | `briarctl` |
| `GET /v1/groups/{groupId}/members` | members who have joined: `author`, `authorStatus`, `creator`, `contactId` (if the member is a contact the hub can see), `visibility` | `briarctl` |
| `GET /v1/groups/{groupId}/invitations` | for each contact, whether it can be invited: `shareable`, `invite_sent`, `sharing` (joined, and also after leaving: Briar cannot invite a member who left back into the same group), `not_supported` (the contact's Briar has no private groups, or has not told the hub yet), `error` | `briarctl` |
| `POST /v1/groups/{groupId}/invitations` `{contactId, text?}` | invite a `shareable` contact to a group the hub created | `briarctl` |
| `GET /v1/groups/invitations` | invitations to groups made by others: `groupId`, `name`, `creator`, `contactId` | `briarctl` |
| `POST /v1/groups/invitations/{groupId}` `{accept}` | accept or decline such an invitation | `briarctl` |
| `GET /v1/groups/{groupId}/messages` | joins and posts, oldest first: `id`, `type` (`join`, `post`), `author`, `authorStatus` (`ourselves` for the hub's own), `parentId`, `timestamp` (the author's clock), `read`, `text` (posts only) | extension |
| `POST /v1/groups/{groupId}/messages` `{text}` | post as the hub; returns the message | extension |
| `POST /v1/groups/{groupId}/messages/read` `{messageId}` | mark a message read (the catch-up of §7.2) | extension |
| WS `GroupMessageAddedEvent` | a join or post from another member, with the fields of a listed message and `groupId`; never the hub's own | extension |
| WS `GroupDissolvedEvent` `{groupId}` | the creator dissolved a group the hub is a member of | extension |

- Invitations to the hub and answers to its invitations already reach the WebSocket and the
  conversation list once private groups are on: a `ConversationMessageReceivedEvent` of type
  `GroupInvitationRequest` (its `sessionId` is the group id) or `GroupInvitationResponse`
  (`shareableId`, `accepted`).
- **Ids.** In JSON, ids are standard base64 like everywhere in the API. In a URL path, a group id is
  URL-safe base64 (RFC 4648 §5, padding optional), because a standard base64 id can contain `/`.
- **Errors**, in the API's existing style: 400 for a missing, empty or too long field (name over 100
  bytes, post or invitation text over 31 744 bytes of UTF-8); 404 for an unknown group, contact or
  invitation; 403 with `{"error": …}` when the state does not allow it: `NOT_CREATOR` (only the
  creator can invite), `NOT_SHAREABLE` with the contact's status, `DISSOLVED` (no posts or
  invitations in a dissolved group).
- **Posts.** A post is signed by the hub, chained to the hub's previous message in the group, and
  timestamped later than it: the patch takes the later of now and the group's latest message time
  plus 1 ms, as the app does. It reads the chain, signs and stores in one database transaction, so
  two posts at once cannot fork the chain. A post has no parent (replies are not used).
- Not in the patch: "Reveal contacts" (members are visible to the creator anyway), replies, read
  flags beyond the catch-up.

The patch lives in our fork of `briar-headless`, on top of the pinned release tag, and is offered
upstream (D29).

### 7.5 `briarctl`

A small command-line tool for the admin, run on the server (e.g. `docker compose exec chatko briarctl …`).
It talks only to the `briar-headless` REST API, with the same token as the extension, and shares no code
with chatko's core or extensions (D24).

| Command | What it does |
|---|---|
| `briarctl link` | print the hub's `briar://` link |
| `briarctl contact add <link> [--alias NAME]`, `contact list`, `contact remove <contact>` | manage the account's contacts |
| `briarctl group create <name>`, `group list`, `group members <group>` | create and inspect groups; `create` prints the id for `chatko.yaml`; `members` also shows the contacts invited but not joined yet |
| `briarctl group invite <group> <contact>…` | invite contacts to a group the hub created |
| `briarctl group dissolve <group>` | dissolve a group (to re-create it without someone) |
| `briarctl invitation list`, `invitation accept <group>`, `invitation decline <group>` | join a group someone else created, or refuse |

Output is plain text by default and JSON with `--json`, so it can be scripted.

## 8. Author labels

The **author label** is the name in front of a relayed message (`NatAda: text`). It is short because
every Meshtastic byte counts. By default:

| Author | Default label |
|---|---|
| an account listed under a **person** in the config | the person's label: `NatAda` |
| any other account | a 2–6 character Latin form of the name the network gives (Telegram name, Briar nickname, Meshtastic long name), made by the label generator below. If the name gives fewer than 2 Latin letters: the network's short name (a Meshtastic node has one), or else the first characters of the account id. Not unique |
| an author relayed by a peer hub (§9.6) | the label the peer already put in front of the text |

The label generator:
1. **Transliterate** the name. Every name is read as Ukrainian, by the official KMU-2010 table.
   Letters of other Cyrillic alphabets are added to it (ы → y, э → e, ъ dropped, ђ → dj, қ → k),
   and a letter with a diacritic is written as its base letter (ё → e, ў → u). Apostrophes inside a
   Cyrillic word are dropped; other Latin letters lose their marks (é → e, ł → l).
2. **Words** are separated by anything but letters, digits, apostrophes and hyphens
   (`Jean-Luc` and `O'Brien` are one word each). Only Latin letters count, so words without any (an
   emoji, a number, Chinese characters) are skipped.
3. **Label**: the first 3 letters of the first word and the first 3 of the second, each capitalized:
   *Наталія Адамчук* → *Nataliia Adamchuk* → `NatAda`. With a single word, its first 6 letters.
4. **Fallback**, when step 3 gives fewer than 2 letters: the network's short name, transliterated,
   with its letters and digits up to 6; else the first 6 letters and digits of the account id.

| Name the network gives (short name, account) | Label |
|---|---|
| Наталія Адамчук | `NatAda` |
| Сергей Петров | `SerPet` |
| Юлія | `Yuliia` |
| Ada Lovelace | `AdaLov` |
| Jean-Luc Picard | `JeaPic` |
| 🦊 Fox | `Fox` |
| Наталія Адамчук-Коваль | `NatAda` |
| Li Wei | `LiWei` |
| 📡 42 (short name `BC1`) | `BC1` |
| none (short name 🦊, node `!a1b2c3d4`) | `a1b2c3` |

People in the config are optional. They give a person one name in every network (their Telegram,
Briar and Meshtastic accounts all signed `NatAda`). To find account ids, the admin can turn on an admin
notice for every account the hub sees for the first time.

The routing script can replace this logic with its own `label(author, target, ctx)` function (§9.5),
e.g. a different label per network, a `~` mark for accounts not in the config, or the full name in
Telegram where space is not an issue. The default is available to it as `default_label(author, ctx)`.

Message format with the default labels:

| Where | Text |
|---|---|
| Into any endpoint | `NatAda: text` |
| Feed | `[BC1] Base Camp: text` (the script's choice) |

Replies, edits and deletions are not mirrored in v1. A reply is sent as plain text.

## 9. Routing

Configuration is split in two (D16):
- **What exists** is declarative, in `chatko.yaml`: extension instances (with channels and PSKs),
  groups and their legs, sources, people, where admin notices go, later peer hubs.
- **Where each message goes** is code, in `config/routing.py`: a Python function the admin writes. It
  sees the incoming message and the installation and returns the targets.

### 9.1 The pipeline

For every incoming message:

1. **Extension**: drops the hub's own posts (the bot's messages, the hub node's packets, the hub's Briar
   posts) and submits the rest with its transport id and author account.
2. **Core, before routing**: de-duplicates by `(endpoint, transport id)` (Meshtastic packets often arrive
   once per gateway); finds the person for the account, if any; recognizes messages relayed by a peer
   hub (§9.6); computes the message **fingerprint** (§9.5); stores the message.
3. **Routing script**: `route(message, ctx)` returns a list of **targets**.
4. **Core, after routing**: applies the invariants (§9.3), computes the author label for each target
   (§8), and writes one outbox row per remaining target.
5. **Outbox worker**: delivers each row through its extension. A failed delivery is retried with
   backoff and survives a restart.

### 9.2 Targets

A target is an endpoint (a leg or a source) with optional overrides of the text or the author label,
e.g. to add a `#street` tag. The script has no I/O: it cannot send anything itself, only return targets.
So the core controls every delivery, and the script is easy to test.

### 9.3 Invariants the core always enforces

The script cannot turn these off. They are what keeps echoes and duplicates out, whatever the script
does:

- The hub's own posts never reach the router (step 1).
- A message is never delivered back to the endpoint it came from.
- A message is delivered to the same endpoint at most once.
- A message that another path already brought in (same fingerprint, §9.5) is routed only once, when
  the admin enables fingerprint de-duplication for that endpoint (default: on for endpoints shared with a
  peer hub).
- Anything a script returns for an endpoint that does not exist is dropped and logged.

### 9.4 Default routing and errors

- Without `routing.py`, the core uses its **default router**: a message from a leg goes to all other
  legs of the same group, and a source goes nowhere. The same function is available to scripts as
  `mirror(message, ctx)`, so a script usually handles a few special cases and ends with
  `return mirror(message, ctx)`.
- `routing.py` is reloaded on change, like the YAML. A script that fails to load keeps the previous one
  running and is reported as an admin notice. If `route()` or `label()` raises for a message, that
  message is handled by the defaults, and the admin is told once per error kind.
- `chatko check-config` loads the script and runs its tests (§9.5) as well.

### 9.5 Helpers for scripts

`chatko.routing_api` is the only package a script imports. Besides `mirror` and `default_label`, it
gives:

- an optional hook: if the script defines `label(author, target, ctx) -> str`, the core uses it for
  every author label instead of `default_label` (§8). The core still limits a label to what the target
  network can show (it counts towards the 200-byte Meshtastic packet);
- the message: source endpoint, group (if the endpoint is a leg), author (account, display name, person
  if any, or an author relayed by a peer hub), text, attachments, fingerprint, time;
- the installation: groups and their legs, sources, people, peers;
- state the core already tracks, e.g. when a node was last heard on a channel (`last_heard`);
- **fingerprint**: a hash of the original author label and the normalized text. Two copies of one
  message that came by different paths (a peer's relay, a late Briar sync, another gateway) have the
  same fingerprint but different transport ids. The label is the default one (§8: a peer's label,
  the person's, or the generated one), never the `label` hook's, and keeps only its letters and
  digits, case-folded, so `~NatAda` matches `NatAda`. The text is the message as a text-only network
  shows it (`[photo] caption`), Unicode-normalized (NFKC), case-folded, with runs of whitespace made
  one space. A message cut short on the way (§6.3) does not match its original;
- `ctx.seen(fingerprint, within=…)` for scripts that want their own duplicate rules;
- a test kit: the admin writes plain `pytest`-style checks next to `routing.py` with a fake installation
  and asserts on the returned targets and labels.

A script is trusted code with the hub's rights, like the config itself: only the admin writes it.

### 9.6 Several hubs (future)

Not in v1, but the design keeps room for it (D17). Example: hub **cloud** (internet: Telegram, Briar,
Meshtastic through the Kyiv broker) and hub **home** (a Raspberry Pi without internet: Briar on the
local Wi-Fi, a physical Meshtastic node over Wi-Fi or BLE).

```
 home Briar ─► home hub ─LoRa─► … ─► Kyiv gateway ─MQTT─► cloud hub ─► Telegram, cloud Briar
            ◄─ (later) a phone gets internet and Briar syncs the same private group with the cloud ─►
```

The same message can reach the cloud twice: relayed by the home hub over the mesh, and later as the
original Briar post. And each hub would see the other's relays as foreign messages and relay them back.
So:
- `chatko.yaml` lists **peers**: the other hub's accounts (node id, Briar author, bot id).
- A message from a peer account is parsed as `NatAda: text`: its author label is the original author,
  not the peer (`CHKH: NatAda: text` never appears).
- Endpoints shared with a peer (the same Briar group, the same private channel) de-duplicate by
  fingerprint (§9.3), with a window long enough for a late Briar sync.
- A message a peer relayed into a shared endpoint is not relayed into that endpoint again.
- Fingerprints can collide when someone really repeats a short text ("ok"). The window and the
  endpoints it applies to are configurable. Whether this is good enough is decided when the second hub
  is built.

## 10. Configuration

Two files in `config/`, both edited only by the admin and reloaded on change:

- `chatko.yaml`: extensions (with channels and PSKs, `dm` node lists and node keys), groups, sources,
  people, the admin notice endpoint, later peers. See [`config.example.yaml`](../config.example.yaml).
- `routing.py`: the routing script (§9). Optional; without it the defaults are used. See
  [`routing.example.py`](../routing.example.py).

If a new version is invalid, the hub keeps the previous one and reports the error as an admin notice.
Secrets (tokens, passwords, PSKs, the hub node's private key) come from environment variables
(`${VAR}` in YAML, values in `.env`), never from the files.

The hub never writes these files. SQLite holds only runtime state: messages, the outbox, transport ids
and fingerprints for de-duplication, when nodes were last heard, accounts already seen.

## 11. Development and deployment

- **Development (now):** everything on the owner's computer. `docker compose` with a local Mosquitto,
  the hub's `meshtasticd`, and a second `meshtasticd` that plays a person's radio, so both `channel` and
  `dm` endpoints can be tested without hardware. When a hardware node is available, it is tested as a
  person's radio, then as our gateway (§6.2), and later as a physical hub node. `briar-headless` is
  built locally. A read-only connection to the Kyiv broker (`lab/spike_kyiv.py`) waits for a claimed
  physical node (D27). The Meshtastic lab is in [`lab/`](../lab/README.md) (image
  `meshtastic/meshtasticd`, pinned tag, amd64 and arm64).
- **Production:** a Linux server (cloud VM, x86-64 or ARM64), the same compose file, which also runs
  our Mosquitto (§6.2, D30). Inbound ports: SSH, and MQTT over TLS (8883) for our gateways. Mosquitto
  allows no anonymous clients: each gateway and each hub node has its own user, limited by an ACL to
  the root topic. Gateway firmware does not check the broker's certificate (`setInsecure`), so TLS
  hides the passwords from eavesdroppers but not from an active attacker; the packets themselves are
  encrypted with channel PSKs or PKI anyway. The hub's nodes reach Mosquitto inside the Docker
  network. Telegram long polling and Tor are outbound. The `meshtasticd` TCP API (4403) has no
  authentication, so it stays inside the Docker network. `meshtasticd` containers need a restart
  policy, because a node reboot ends the process.
- Volumes: `config/` (chatko.yaml, routing.py, meshtasticd configs) and `data/` (SQLite, Briar and
  meshtasticd state: settings, the node database with members' keys, and the node's own key unless
  `private_key` is set). Secrets live in `.env`. `config/` and `data/` are backed up daily.
- `briar-headless` asks for its account password on every start, because the password encrypts the
  database key. Its container entrypoint gives it `BRIAR_PASSWORD` (and the nickname on the first
  start) and writes `BRIAR_AUTH_TOKEN` as the API token, so it starts unattended (D28). Its API
  (port 7000) stays inside the Docker network.
- `docker stop` kills `meshtasticd` after 10 s (it does not exit on `SIGTERM`) without saving, so
  whatever the node learned but has not saved yet is lost (learned keys are saved at most once a
  minute); the hub keeps what it needs as favorites or in its own state (§6.2).
- Hosting candidate: Oracle Cloud Always Free (Ampere A1, ARM64). Oracle reclaims Always Free instances
  that look idle for 7 days. A Pay As You Go account keeps the free limits and is not reclaimed.

## 12. Plan

The phases are split into working sessions in [roadmap.md](roadmap.md).

| Phase | What | Done when |
|---|---|---|
| 0. Spikes | S1 Briar relay with 3 phones. S2 local Meshtastic lab. S3 briar-headless build and API. See [spikes.md](spikes.md) | Every (verify) that affects v1 has an answer |
| 1. Core | endpoints, groups, people, labels, routing engine and routing API, outbox, admin notices, config and routing script, SQLite; extension API and the contract test suite; a fake extension | the core is fully tested with fake extensions and a sample routing script |
| 2. Telegram | group and private-chat endpoints, allowed chats | two Telegram groups work independently; foreign groups are left |
| 3. Meshtastic | `channel` and `dm` endpoints, provisioning from the config, splitting, `LongFast` source | radio ⇄ Telegram in the local lab, then on the Kyiv mesh through our own gateway |
| 4. Briar | private-group API patch, `briarctl`, Briar endpoints | Briar group ⇄ Telegram group ⇄ Meshtastic in the cloud setup |
| 5. Later | a physical node, several hubs (§9.6), direct messages, web UI, Signal, SMS, MeshCore, monitoring | — |

## 13. Other networks worth adding later

1. **Web UI (PWA)**: another endpoint for people, and the main fallback if Telegram fails.
2. **SMS**: for when mobile data is down but voice and SMS still work. Outbound through a Ukrainian SMS
   API; two-way through an Android phone acting as a gateway. It costs money per message.
3. **MeshCore**: a second LoRa mesh growing in Ukraine. mr-tbot/mesh-api has code for it.
4. **Signal** through [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) (as
   mr-tbot/mesh-api does), **Email / Delta Chat**, **Reticulum/LXMF** (Sideband) for technical members.
