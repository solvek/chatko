# chatko design

Status: **draft**; phase 1 (the core) is written and reviewed; phase 2 (Telegram) is done, with its live test; phase 3 (Meshtastic) has the connection to the hub's node, its provisioning and the `channel` endpoints, and the `dm` endpoints come next. This document describes **behaviour**. The
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

v1 is one hub in the cloud that keeps a Briar group, a Telegram group and Meshtastic (direct messages
to several nodes; `channel` endpoints exist but v1 does not use them, D59) in sync (D20).

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Installation** | One running hub with its configuration and its enabled extensions. |
| **Extension** | A plug-in that connects chatko to one network or service: `telegram`, `meshtastic`, `briar`, later others. The core knows no network by name. An extension can be added or removed without touching the core. |
| **Extension instance** | A configured copy of an extension, with its own name, e.g. `telegram`, `kyiv` and `lab` (two meshtastic instances on different MQTT brokers), `briar`. |
| **Endpoint** | One place where an extension instance reads and posts messages: a Telegram chat, a Briar private group, a Meshtastic channel, a set of Meshtastic nodes reached by DM. |
| **Recipient** | One of several addressees that an endpoint reaches separately: a node of a Meshtastic `dm` endpoint. Each gets its own delivery, confirmation and retries (§9.1). Most endpoints are one place and have none. |
| **Group** | An independent chat room: a named set of **sites**. |
| **Site** | An endpoint that belongs to a group: one place where the group lives. |
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
| Mosquitto | Our MQTT broker, in the same compose file; the lab and tests use it, and so would gateways of our own (§6.2). Production does not need it while the hub's node uses the Kyiv broker (D58) | The Kyiv broker gives logins only to claimed physical nodes (D27), so until the owner has one, the hub's node uses this broker |
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
  admin notices; the person must have pressed Start once). A group's id is negative; a private
  chat's id is the person's user id. One instance is one bot, polling the Bot API (no webhook,
  so the hub needs no inbound port).
- The bot serves only the chats listed in the config. In any other group or channel it ignores the
  messages, and the first one tells the admin the chat's title and id (once per chat until the
  config changes), so the admin can add the chat to the config or remove the bot. It does not leave
  such a chat: it may be a configured group that has just changed its id (below), and Telegram
  reports the bot in the new chat before it reports the change (D45). Private messages from chats
  not in the config are ignored and only logged with the chat id, which is how the admin learns
  their own id for a private-chat endpoint. To keep strangers from adding the bot to their groups,
  the admin turns off joining groups in BotFather (`/setjoingroups` → Disable) once the groups are
  set up, and turns it on for a while to add the bot to a new one.
- **A group that becomes a supergroup gets a new chat id.** Telegram does this when a group's
  settings need a supergroup: making the bot an admin did it (S18), and so may a public link,
  visible history or many members. The bot follows the group to its new id until the hub restarts,
  and an admin notice asks the admin to put the new id into the config (D44).
- When a group removes the bot, or a person blocks it in a private chat, the admin is told; the
  messages for that endpoint wait and are retried until the bot is back, or until they are given
  up (§9.1). So do messages that the bot cannot post for another reason the admin can fix (it is
  not a member, the chat does not exist, the token is refused).
- In: the text of a message, or its attachments as placeholders with the caption as text (a
  sticker's emoji, a venue's or a poll's title stands in for a caption). Service messages (joins,
  pins, title changes), edits and the bot's own messages are not relayed. The author is the user,
  or the chat for a message posted as a chat (an anonymous admin, a channel); its display name is
  the first and last name (the chat's title), and the username is its short name (§8). The
  `/start` that the Telegram app sends when a person presses Start in a private chat is dropped.
- Out: `NatAda: text` as plain text, without markup. A text longer than Telegram's limit (4096
  characters) is cut with `…`.
- A message that another network got only in part (§6.3) gets the bot's ✍ reaction in Telegram.
  Bots can react only with Telegram's standard reactions, which have no ✂️ (D44).
- Who may write in a Telegram group is decided by its Telegram admins.
- The bot must see all messages: disable privacy mode in BotFather (`/setprivacy` → Disable)
  before adding the bot to a group (the setting applies to groups the bot joins later). It needs
  no admin rights; making it an admin turns a basic group into a supergroup with a new id (D45).
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
  old one fail until that radio is given the new key (spike S2). The hub logs its public key when
  its node is first ready.
- The node's channels and their PSKs are set in the config, and the hub provisions the node from it. The
  admin gives the PSK (a channel URL or QR code from the Meshtastic app) to the people who should have it.
  Removing someone from a channel means a new PSK on all radios.
- Provisioning (D25, D46) goes through the node's admin API over the same connection the hub uses
  for messages, every time the hub connects to its node: names, region, `ignore_mqtt` off and "OK to
  MQTT" on, the private key if configured, the MQTT client with the node's own login, and every
  channel slot: the configured channels in order (the first is the primary channel) with uplink and
  downlink on, the other slots off. It writes only what differs from the node's current settings,
  in one settings transaction, sends one admin message at a time and waits for the node's response
  to each (the node drops the oldest when more than 4 wait, D26; without a response within 10 s
  the hub connects again), and reconnects when the node reboots (a commit always reboots it; a
  `meshtasticd` reboot ends the process and its container restarts it). Then it adds the
  configured `contacts` (below), and only then does the node carry messages. A setting the node
  refuses, or that still differs after the reboot that saved it, is reported to the admin; the hub
  then goes on with the node as it is rather than reboot it again. `meshtasticd`'s own YAML sets only
  the simulated radio, the MAC address (which fixes the node id) and logging.
- The hub keeps its node connected: it tries again with a growing pause (up to 30 s) and tells the
  admin when the node has been unreachable for 2 minutes, or when the node keeps closing the
  connection, which means another client is connected to it (below).
- A channel is matched over MQTT by its **name** (the topic `<root>/2/e/<name>/<gateway>` and the
  envelope carry it; the packet carries only a hash), then decrypted with its PSK. A private channel must
  have the same name and PSK on the hub's node and on every radio. A name has at most 11 bytes and
  no spaces. The config names the primary channel too (`LongFast`): an unnamed primary channel
  shows the modem preset's name, so naming it so changes neither its topic nor its hash.
- `meshtasticd` serves one API client at a time: a new connection drops the previous one. The hub is the
  only client of its node; the admin does not connect the Meshtastic app or CLI to it while the hub runs.
- A node has up to 8 channels. Channel 0 is usually the primary channel of the mesh (e.g. `LongFast`;
  on the Kyiv mesh it has a non-default PSK), used as a source. The other channels are private group channels with their own PSK.
- A group may have several Meshtastic sites, even on different instances (brokers).
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

- A `channel` endpoint names one of the node's channels (`channel: family`); two endpoints cannot
  share one. A broadcast text on it from another node comes in, with the transport id
  `<node id>/<packet id>` (`!a1b2c3d4/0973632b`). A packet that arrives through several gateways
  keeps its sender and id, and the hub's node hands it over once anyway (spike S2); the core drops
  a repeat by the transport id. The node's own packets, tapbacks (a reaction, which the Meshtastic
  apps send as an emoji text), blank texts and non-text packets are not relayed. A message goes
  out as one broadcast per part (§6.3), each once the broker echoed the one before (the node's
  implicit ACK). Without the echo within 25 s, or with the node's NAK, the delivery is retried
  later, from the first part that did not get through; while the node is connecting or
  provisioning, a delivery waits for it up to 10 s, and when it is ready again the deliveries
  waiting for its endpoints are tried at once.
- Every packet from another node, of any kind, tells the hub that the node was heard: at the
  `channel` endpoint it came on; at every `dm` endpoint that lists the node, if the packet was
  addressed to the hub's node (a direct message, an ACK); or else at no endpoint (a packet on a
  channel that is no endpoint, one the node could not decrypt, a direct packet from a node of no
  `dm` endpoint). The hub is told once a minute at most per node and place, which is precise
  enough for `last_heard` (§9.5).
- Each node of a `dm` endpoint is a **recipient** (§9.1): the hub delivers to, waits for the ACK of
  and retries each node on its own, so a radio that is away holds up nobody else, and the messages
  for it wait, in order, until it is heard again. A message goes out as one direct message per
  part (§6.3), each once the node acknowledged the one before; a retry goes on from the first part
  that got no ACK.
- A direct message to the hub's node from a listed node comes in at the `dm` endpoint that lists
  it, with the transport id `<node id>/<packet id>`, and the node as the recipient that posted it
  (§9.1). A node listed in several `dm` endpoints: its direct messages go to the first of them in
  the config; the routing script can send them elsewhere. Direct messages from nodes not listed
  anywhere are logged and dropped, and so are direct messages that are not PKI-encrypted: one
  encrypted with a channel key could come from anyone who has that key, under any node id (the
  Kyiv mesh's primary key is public), and current firmware drops them anyway. Tapbacks and blank
  texts are dropped as on a channel.
- A direct message from a listed node reaches the group's other sites and, as direct messages, the
  endpoint's other nodes, so the radios of one `dm` endpoint hear each other through the hub (D38).
  It never goes back to the node that sent it. With N nodes, each message from a radio costs N−1
  direct messages, each with its ACKs; a script can narrow them (`recipients`, §9.2).
- **Keys for `dm`.** A text direct message needs the public keys on both sides: the hub's node refuses
  to send without the person's key, and radios drop old channel-encrypted direct texts. Keys travel in
  NodeInfo on the primary channel (each node broadcasts it 30 s after boot and every 3 h), and a node
  that cannot decrypt a direct message answers with a NAK after which the sender's node sends it its
  NodeInfo at once. Asking a node for its NodeInfo works only now and then: a node answers at most once
  per 10 minutes, and once per 12 hours to the same asker (spike S2). So the config may give the
  public key of each `dm` node (`contacts`), which the hub adds to its node as a favorite contact. A
  listed node whose key the hub's node learned by itself is made a favorite too, as soon as the node
  has the key (from its database when the hub connects, or from a NodeInfo it takes, also after the
  endpoints change): favorites are saved at once (other learned keys are written at most once a
  minute and can be lost on a restart) and are never evicted from the node database (which fills up
  with the nodes of a busy primary channel).
- **Key pinning.** A node keeps the first key it learned for a node and ignores NodeInfo with another
  key. When a person resets their radio, the hub's node keeps the old key: the radio answers the hub's
  direct messages with NAK `NO_CHANNEL` (with `PKI_UNKNOWN_PUBKEY` while it does not know the hub yet),
  and its NodeInfo with the new key reaches the hub but is dropped by the node. The hub posts an admin
  notice on either sign, `NO_CHANNEL` or a NodeInfo with another key from a node of a `dm` endpoint
  (that one names the new key, to be checked with the radio's owner, since anyone with the primary
  channel's key can send a NodeInfo), once per node until the node acknowledges a message again; the
  admin puts the new key into `contacts`, which replaces the pinned one. The same happens on every
  radio if the hub's key changes, hence `private_key` (§6.1); then the radio's owner removes the hub
  from the radio's node list, and the radio learns the new key from the hub's next NodeInfo.
- **ACKs.** `dm` messages ask for an acknowledgement, and ACKs come back through MQTT. Only an ACK from
  the listed node means delivered. The hub's own node also reports an "implicit" ACK as soon as the
  broker echoes the packet back, which only means the broker has it; a text that got not even that was
  dropped by the node (e.g. sent too soon, §6.3). Without the real ACK the node publishes the packet
  twice more (about 7.5 s apart) and gives up after about 23 s. A delivery waits up to 30 s for the
  answer, and then (D26, D49):

  | The answer | Means | Tried again |
  |---|---|---|
  | the node's ACK | delivered | (the next part follows) |
  | none, not even the implicit ACK | the hub's node dropped the text | after the outbox's backoff |
  | NAK `MAX_RETRANSMIT` from the hub's node, or only the implicit ACK | the radio is away | as soon as any packet from it is heard |
  | NAK `PKI_SEND_FAIL_PUBLIC_KEY` from the hub's node | the hub's node has no key of the radio | as soon as the hub's node has its key (its NodeInfo) |
  | NAK `PKI_UNKNOWN_PUBKEY` from the radio | the radio has no key of the hub; the hub's node sends it its NodeInfo at once | after the outbox's backoff (10 s first) |
  | NAK `NO_CHANNEL` from the radio | the keys do not match (key pinning, above) | after the outbox's backoff, and once the admin fixed the config |
  | any other NAK | | after the outbox's backoff |

  The outbox's backoff (10 s, doubling up to an hour, §9.1) stays the fallback for a radio whose
  packets the hub does not hear, and for a radio that comes back quietly: one restarted less than
  10 minutes after its last NodeInfo sends none at boot, so the hub hears it only when it sends
  something (a text, telemetry, a position; D50). When the hub's node is ready again (after a
  reconnect, or as a new instance after the config changed) the hub tries every waiting delivery
  of the instance at once.
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
- **The v1 path: the Kyiv broker and the owner's physical nodes (D58).** The owner has several physical
  `EU_433` nodes in the Kyiv mesh. They only talk over LoRa to other nodes of the mesh, and send and
  receive our messages there. At least one of them has MQTT on and is **claimed** in the community's
  registry (D27), which gives it a login (its node id in hex) and the root topic `node/<node id>` on
  `mqtt.meshtastic.kyiv.ua`. That node is the gateway: it uplinks what it hears to the broker and
  downlinks what arrives, with the mesh's primary channel (and the group's private channel, if `channel`
  endpoints are used, which v1 does not do, D59), uplink and downlink on, "Ignore MQTT" off. The hub's virtual node connects to the same broker with the same
  login and root topic, so it hears the gateway's packets and the gateway hears its own (the config
  needs only the login in `.env`: `KYIV_MQTT_USER`, `KYIV_MQTT_PASSWORD`). No gateway and no broker
  of our own are needed, and nothing but SSH has to be open on the server. **Not yet tried:** the
  login does not exist yet. Until it does, production uses our own Mosquitto inside the compose
  network, which no radio can reach, so Meshtastic endpoints are configured but carry nothing. Whether
  the Kyiv broker passes packets between clients under one `node/<id>` root, and whether it carries
  PKI direct messages, is the first question of §6.6 and decides `dm`. Our own broker with a gateway
  of our own (D30) stays possible: it is the lab's setup, and `MQTT_TLS_BIND=0.0.0.0` publishes
  Mosquitto's TLS port (deploy/README.md).
- **v1 uses only `dm` (D59).** It needs no private channel and no PSK given to anyone, and a gateway
  needs only the primary channel, so any gateway on the hub's broker will do. Direct messages are
  confirmed by an ACK and retried; a `channel` broadcast is not. `channel` stays in the code and the
  tests, for a group that wants a broadcast later. With only `dm`, the Kyiv broker's carrying of PKI
  packets (§6.6) decides whether v1 works at all.
- Both kinds can be used in one group. Then a person with a node on both gets the message twice, unless
  the routing script skips `dm` for nodes recently heard on the channel (`last_heard` and a target's
  `recipients`, §9.5; `tests/integration/test_meshtastic_lab_relay.py` and `lab/three-networks/routing.py`
  do it).
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
- Longer text is split on spaces into `NatAda (1/3): …`, at most 3 parts, each filled as far as it
  goes; a word longer than a part is cut between characters, and line breaks inside a part stay.
  Anything beyond that is truncated with `…`, and the source endpoint is told about it (a delivery
  report, §9.1) where the network allows (in Telegram, the bot's ✍ reaction, §5).
- A label longer than 39 bytes (a node's longest long name) is cut with `…`.
- Non-text content goes out as a placeholder: `NatAda: [photo] caption`.
- The hub sends no faster than one packet every few seconds per node (configurable), to be a good
  neighbour on a busy mesh. The interval is per hub node (all its endpoints together) and at least
  2.5 s: the node silently drops a text from the hub that it handles less than 2 s after the
  previous one (spike S2), and it may handle a text some time after the hub sent it (D50).
- A direct message carries 12 bytes more than a channel packet (PKI), so it holds at most about
  220 bytes of text (a channel packet about 230); the 200-byte limit fits both.

### 6.4 Authors

A Meshtastic message carries the sender's node id and, once NodeInfo was heard, the node's long and
short name (a direct message also carries the sender's public key). The author label comes from these
(§8). They are the names the hub's node has for it: from its node database, and from the NodeInfo it
takes since (not one it drops for a key other than the pinned one, §6.2). Until NodeInfo is heard,
only the node id (`!c4a7b002`) is known, and the label is made from it (`c4a7b0`): the Python
library then shows a placeholder name `Meshtastic b002`, which is not the node's name and is not used
as one.

### 6.5 Feeds

- The config declares the channel as a **source** (e.g. `longfast: { ext: kyiv, channel: LongFast }`).
- The routing script sends its messages to any endpoint, e.g. a private chat with the bot, as
  `[BC1] Base Camp: text`.
- A feed is one-way: messages in the target endpoint are routed by their own rules, not back to the
  source, unless the script says so.
- Through the Kyiv broker (§6.2) the hub hears the mesh's `LongFast` as the owner's gateway nodes
  hear it; with a broker of our own, only what our gateways hear.

### 6.6 Open questions about the Kyiv mesh

v1 reaches the Kyiv mesh through the Kyiv broker and the owner's claimed nodes (§6.2, D58), so these
now decide whether and how well it works. The questions were drafted for the community in session
S04; the answers go into spikes.md (S2, part 3) and here, once a node is claimed and the login is
there (the field test, S23).

| Question | What it decides |
|---|---|
| What the broker's "enable routing" does with packets published under `node/<id>`: are they bridged to other nodes' topics, and may a client read them? | whether a hub on the Kyiv broker hears the mesh and is heard by it at all |
| The broker's policy for PKI direct messages (`…/2/e/PKI/…`) and for downlink | whether `dm` works through the community's gateways |
| The firmware versions and settings of the gateways: downlink on the primary channel, "Ignore MQTT", rebroadcast mode | whether a gateway learns the hub's node and downlinks its packets (§6.2) |
| How many relays run with "Ignore MQTT" on, and how many radios have "OK to MQTT" on | how far the hub's packets travel over the air, and whether replies come back |
| Whether the community accepts a bot node on the mesh | the hub's node is visible on `LongFast` through our gateway in v1 too (NodeInfo, ACKs), so the owner asks before the field test (S23) |
| Whether the broker accepts the hub as a second client under the claimed node's login (its client id differs), and whether the gateway id in a packet must match the topic's `node/<id>` (D61) | whether the hub can share the physical node's login or needs its own |
| The credentials sent first were for another broker (`mqtt.wikimesh.in.ua`), under a shared root `kyiv` and with encryption off (D62): is that the access meant for us, and can the hub's node read decoded packets and not only encrypted ones | which broker and topic layout v1 uses, and whether `dm` can work on it |

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
between members who are contacts and reveal that (§7.3). The recommended setup is the one people know
from Telegram: **a person creates the group** in the Briar app and invites the others and the hub
(D52). The hub syncs the group with its creator, and directly with every other member who is its
contact once the hub has revealed that relationship (`briarctl group reveal`), so the creator's phone
is not the hub's only way into the group. A group that **the hub creates** with `briarctl` is the
fallback: its creator is online all the time, which keeps the group alive even when no person's phone
is (§7.3).

1. **Contacts.** For each person, `briarctl contact add briar://…` adds their link, and the person adds
   the hub's link (`briarctl link`) in the Briar app ("Add contact at a distance"). A contact at a
   distance needs **both** sides to add each other's link: with only one side, nothing happens; once
   both have, the contact appears in seconds (spike S3). Such contacts stay "unverified" in Briar
   (only contacts added in person are verified), which does not affect syncing.
2. **Group.** A person creates the group in the app and invites the hub. `briarctl invitation list`
   shows the invitation with the group's id, `briarctl invitation accept <group>` joins it, and
   `briarctl group reveal <group> <contact>…` reveals the hub's relationship with the members who are
   its contacts. As the fallback, `briarctl group create "Family"` prints the new group's id and
   `briarctl group invite <group> <contact>…` invites people, who accept in the app.
3. **Endpoint.** The admin puts the group id, as `briarctl` prints it, into `chatko.yaml` (a site of
   a group, or a source). From then on the extension reads and posts there.

Briar ids (groups, authors) are 32 bytes. `briarctl`, `chatko.yaml` and chatko's logs write them in
URL-safe base64 without padding (43 characters of `A–Z a–z 0–9 - _`), the form the API takes in URL
paths (§7.4).

A hub can be in any number of Briar groups, and a message can be routed into several of them.

The extension posts routed messages into a Briar group as `NatAda: text`. A post holds up to
31 744 bytes of UTF-8 text, far more than any other network sends. Posts reach the other Briar members
natively. The extension copies them to other endpoints and never echoes them back into their own group
(§9.3).

Briar cannot remove a member from a private group (spike S1, D15). To cut someone off, the creator
dissolves the group and creates a new one without them: a person in the app, the admin with
`briarctl group dissolve`, `create`, `invite` for a group the hub created. The admin then changes the
endpoint in `chatko.yaml`. History on phones is lost, and every remaining member must reach the
creator once to accept the new invitation (an invitation is not relayed by other members).

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
  Messages for it are then `Failed`, not held: the group does not come back (D54). The unread posts
  that were already in it are still relayed.
- **Not a member.** A configured group that the hub has not joined (the invitation is not accepted
  yet) is reported to the admin with a hint to run `briarctl invitation accept`. Messages for it
  wait, in order, until the hub joins (D54).

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
  through that person's phone, and directly with the other members who are its contacts once the
  hub (`briarctl group reveal`) or they have revealed the relationship. In S24 a post went from the
  hub to such a member while the creator was stopped.
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
controllers. The patch (D29, D51) switches private groups on and adds a thin REST layer that makes
the same calls as the app. Briar sets the private-group clients up for an existing account at its
next start and tells its contacts, so an existing account needs no migration: in S24 a phone showed
the hub as "not supported" for invitations while it ran upstream, and could invite it as soon as it
was connected again after the switch to the patched peer, on the same data.

| Endpoint | What it does | Used by |
|---|---|---|
| `GET /v1/groups` | the account's private groups: `id`, `name`, `creator` (author), `ourGroup` (the hub is the creator), `dissolved` | both |
| `POST /v1/groups` `{name}` | create a group with the hub as its creator; returns the group | `briarctl` |
| `DELETE /v1/groups/{groupId}` | the creator dissolves the group, a member leaves it; either way it disappears from the hub with its history | `briarctl` |
| `GET /v1/groups/{groupId}/members` | members who have joined: `author`, `authorStatus`, `creator`, `contactId` (if the member is a contact of the hub), `visibility` of the relationship in the group (`visible`, `invisible`, `revealed_by_us`, `revealed_by_contact`) | `briarctl` |
| `POST /v1/groups/{groupId}/members/reveal` `{contactId}` | reveal the hub's relationship with a member who is its contact, so the two sync the group directly (§7.3); nothing changes if it is visible already | `briarctl` |
| `GET /v1/groups/{groupId}/invitations` | for each contact, whether it can be invited: `shareable`, `invite_sent`, `sharing` (joined, and also after leaving: Briar cannot invite a member who left back into the same group), `not_supported` (the contact's Briar has no private groups, or has not told the hub yet), `error` | `briarctl` |
| `POST /v1/groups/{groupId}/invitations` `{contactId, text?}` | invite a `shareable` contact to a group the hub created; returns the contact's new status | `briarctl` |
| `GET /v1/groups/invitations` | invitations to groups made by others: `groupId`, `name`, `creator`, `contactId` | `briarctl` |
| `POST /v1/groups/invitations/{groupId}` `{accept}` | accept or decline such an invitation | `briarctl` |
| `GET /v1/groups/{groupId}/messages` | joins and posts, oldest first: `id`, `groupId`, `type` (`join`, `post`), `author`, `authorStatus` (`ourselves` for the hub's own), `parentId`, `timestamp` (the author's clock), `read`, `text` (posts only) | extension |
| `POST /v1/groups/{groupId}/messages` `{text}` | post as the hub; returns the message | extension |
| `POST /v1/groups/{groupId}/messages/read` `{messageId}` | mark a message read (the catch-up of §7.2) | extension |
| WS `GroupMessageAddedEvent` | a join or post from another member, as listed; never the hub's own | extension |
| WS `GroupDissolvedEvent` `{groupId}` | the creator dissolved a group the hub is a member of, or the admin removed that creator from the hub's contacts | extension |

- Invitations to the hub and answers to its invitations already reach the WebSocket and the
  conversation list once private groups are on: a `ConversationMessageReceivedEvent` of type
  `GroupInvitationRequest` (its `sessionId` is the group id) or `GroupInvitationResponse`
  (`shareableId`, `accepted`).
- **Ids.** In JSON, ids are standard base64 like everywhere in the API. In a URL path, a group id is
  URL-safe base64 (RFC 4648 §5, padding optional), because a standard base64 id can contain `/`.
- **Errors**, in the API's existing style: 400 for a missing, empty or too long field (name over 100
  bytes, post or invitation text over 31 744 bytes of UTF-8); 404 for an unknown group, contact or
  invitation, and for a group id that is not one of the hub's private groups; 403 with
  `{"error": …}` when the state does not allow it: `NOT_CREATOR` (only the creator lists and sends
  invitations; a dissolved group is always someone else's), `NOT_SHAREABLE` with the contact's
  status, `NOT_MEMBER` (only a member can be revealed), `DISSOLVED` (no posts in a dissolved group).
- **Posts.** A post is signed by the hub, chained to the hub's previous message in the group, and
  timestamped later than it: the patch takes the later of now and the group's latest message time
  plus 1 ms, as the app does. It reads the chain, signs and stores in one database transaction, so
  two posts at once cannot fork the chain. A post has no parent (replies are not used).
- Not in the patch: replies, read flags beyond the catch-up.

The patch lives in our fork of `briar-headless`, on top of the pinned release tag (the branch
`1664-private-group-api` on `release-1.5.21`), and is offered upstream (D29, D53). `deploy/briar/`
builds the image from a checkout of the fork, for both the server and `lab/briar/`; its README
documents every endpoint and event.

### 7.5 `briarctl`

A small command-line tool for the admin, run on the machine that has the hub's `briar-headless`
(`uv run briarctl …`, or from inside chatko's container once the hub runs in one). It talks only to the
`briar-headless` REST API, with the same token as the extension, and shares no code with chatko's core
or extensions (D24, D55).

| Command | What it does |
|---|---|
| `briarctl link` | print the hub's `briar://` link |
| `briarctl contact add <link> [--alias NAME]`, `contact list`, `contact remove <contact>` | manage the account's contacts; `list` also shows the contacts still being added, and `remove` also stops adding one of those (the API's only way to drop one that failed) |
| `briarctl invitation list`, `invitation accept <group>`, `invitation decline <group>` | join a group a person created (the usual way, D52), or refuse; `list` shows the group's id for `chatko.yaml` |
| `briarctl group list`, `group members <group>` | inspect groups; `members` shows which members are the hub's contacts and whether that is revealed, and for a group the hub created also the contacts invited but not joined yet |
| `briarctl group reveal <group> <contact>…` | reveal the hub's relationship with members who are its contacts, so they sync the group directly (§7.3) |
| `briarctl group create <name>`, `group invite <group> <contact>… [--text NOTE]` | the fallback: create a group with the hub as its creator (prints the id for `chatko.yaml`) and invite contacts to it |
| `briarctl group dissolve <group>` | dissolve a group the hub created (to re-create it without someone), or leave a group someone else created |

- **Settings.** The API is at `--url` or `$BRIARCTL_URL` (default `http://127.0.0.1:7000`). The token is
  in `$BRIAR_AUTH_TOKEN` (the name in chatko's `.env`) or in the file given with `--token-file`; it
  is never an argument, which other users could read in the process list, and no message shows it.
- **Arguments.** A `<group>` is an id in either base64 form, with or without padding; a `<contact>` is
  its id, or its alias or name when only one contact has it. A command for several contacts goes on
  after one fails, prints why for each, and exits with 1 at the end.
- **Output.** Plain text by default, JSON with `--json`, so it can be scripted. Ids are printed in the
  URL-safe form of §7.1, ready for `chatko.yaml`.
- **Asking first.** `contact remove` and `group dissolve` cannot be undone, so they ask, and `--yes`
  skips the question; with no terminal to answer they do nothing.
- **Exit codes.** 0 done; 1 the call or some of its items failed (the message is on stderr); 2 wrong
  arguments or settings.

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

The routing script can replace this logic with its own `label(message, target, ctx)` function (§9.5),
e.g. a different label per network, a `~` mark for accounts not in the config, the full name in
Telegram where space is not an issue, or the node's short and long name for a feed. The default is
available to it as `default_label(author, ctx)`.

Message format with the default labels:

| Where | Text |
|---|---|
| Into any endpoint | `NatAda: text` |
| Feed | `[BC1] Base Camp: text` (the script's choice) |

Replies, edits and deletions are not mirrored in v1. A reply is sent as plain text.

## 9. Routing

Configuration is split in two (D16):
- **What exists** is declarative, in `chatko.yaml`: extension instances (with channels and PSKs),
  groups and their sites, sources, people, where admin notices go, later peer hubs. The admin names
  every site and source (`family.telegram`, `longfast`); the script and the hub's state refer to them by
  that name (D34). What a site is in its network (a chat id, a channel, a list of nodes) is up to
  its extension, and the core never reads it.
- **Where each message goes** is code, in `config/routing.py`: a Python function the admin writes. It
  sees the incoming message and the installation and returns the targets.

### 9.1 The pipeline

For every incoming message, one at a time, in the order the extensions submit them:

1. **Extension**: drops the hub's own posts (the bot's messages, the hub node's packets, the hub's Briar
   posts) and submits the rest with its transport id and author account.
2. **Core, before routing**: de-duplicates by `(endpoint, transport id)` (Meshtastic packets often arrive
   once per gateway); finds the person for the account, if any; recognizes messages relayed by a peer
   hub (§9.6); computes the message **fingerprint** (§9.5). If the endpoint de-duplicates by
   fingerprint and the same message came in lately by another path (§9.3), the message is stored
   but not routed.
3. **Routing script**: `route(message, ctx)` returns a list of **targets**.
4. **Core, after routing**: applies the invariants (§9.3), computes the author label for each target
   (§8), and stores the message together with one outbox row per remaining target; for an endpoint
   with **recipients** (the nodes of a `dm` endpoint), one row per recipient. The message and its
   rows are stored in one go, so after a crash there is never a message without its rows. Only then
   does the extension hear that the hub has the message.
5. **Outbox worker**: delivers each row through its extension. The rows of one endpoint (or one
   recipient) go one at a time, in the order the messages came in, and a row waiting for a retry
   holds back the newer ones, so messages never overtake each other. A failed delivery (including
   an extension error or no answer within 2 minutes) is retried with backoff (10 s, doubling up to
   1 h), or as soon as the extension says the place is reachable again (a radio heard again), and
   survives a restart. When an extension instance (re)starts, the rows waiting for it are tried at
   once. A delivery that still fails when its message is 3 days old is given up, and one to an
   endpoint that the config no longer has fails. When a delivery ends, the extension of the endpoint the message came from gets a **delivery
   report**, e.g. to mark a message that was cut short (§6.3).

### 9.2 Targets

A target is an endpoint (a site or a source) with optional overrides of the text or the author label,
e.g. to add a `#street` tag, and for an endpoint with recipients the ones to deliver to (default:
all). The script has no I/O: it cannot send anything itself, only return targets. So the core
controls every delivery, and the script is easy to test.

### 9.3 Invariants the core always enforces

The script cannot turn these off. They are what keeps echoes and duplicates out, whatever the script
does:

- The hub's own posts never reach the router (step 1).
- A message is never delivered back to where it came from: the endpoint, or for an endpoint with
  recipients the recipient that posted it (the node that sent a direct message). The endpoint's other
  recipients may get it. If the extension does not say which recipient posted, the whole endpoint
  is excluded (D38).
- A message is delivered to the same endpoint (to each of its recipients) at most once.
- A message that another path already brought in (same fingerprint, §9.5) is routed only once, when
  the admin enables fingerprint de-duplication for the endpoint it arrives at, with a time window
  (default: on for endpoints shared with a peer hub). A copy that arrives within the window is
  stored, but not routed.
- Anything a script returns for an endpoint that does not exist is dropped and logged; recipients
  that an endpoint does not have are ignored.

### 9.4 Default routing and errors

- Without `routing.py`, the core uses its **default router**: a message from a site goes to all other
  sites of the same group and, from one recipient of a site (a `dm` node), to the site's other
  recipients; a source goes nowhere. The same function is available to scripts as
  `mirror(message, ctx)`, so a script usually handles a few special cases and ends with
  `return mirror(message, ctx)`.
- `routing.py` is reloaded on change, like the YAML; the same code read again changes nothing. A
  script that cannot be read or fails to load (a syntax error, an exception or an `exit()` while
  it runs, no `route(msg, ctx)`, a `route` or `label` that does not take the hook's arguments)
  keeps the previous one running, or the defaults at start, and is reported as an admin notice
  with the line it failed at; so does a script written for a routing API version this hub does
  not support (§9.5). A removed script leaves the defaults running.
- Each function falls back on its own default. If `route()` raises (or calls `exit()`) for a
  message or returns something that is not a list of targets, that message goes by `mirror`; if
  `label()` raises or returns no label (not a string, or blank), that target gets
  `default_label`. The error is logged, and the admin is told once per error kind (the function,
  the exception type and the script line it came from) until the script changes (D39).
- `chatko check-config` loads the script and runs its tests (§9.5) as well.

### 9.5 Helpers for scripts

`chatko.routing_api` is the only package a script imports. Besides `mirror` and `default_label`, it
gives:

- an optional hook: if the script defines `label(message, target, ctx) -> str`, the core uses it for
  every author label instead of `default_label` (§8), except where a target sets its own label. It
  gets the whole message, so a label can depend on where the message came from. The target's
  extension still shortens a label its network cannot show (it counts towards the 200-byte
  Meshtastic packet);
- the message: source endpoint, group (if the endpoint is a site), author (account, display name, person
  if any, or an author relayed by a peer hub), the recipient that posted it (a `dm` node), text,
  attachments, fingerprint, time;
- the installation: groups and their sites, sources, endpoints by name (`family.radio`), people, the
  extension type of an endpoint (`meshtastic`), the recipients of an endpoint, later peers;
- state the core already tracks, e.g. when a node was last heard on a channel (`last_heard`): any
  packet counts, not only a message;
- **fingerprint**: a hash of the original author label and the normalized text. Two copies of one
  message that came by different paths (a peer's relay, a late Briar sync, another gateway) have the
  same fingerprint but different transport ids. The label is the default one (§8: a peer's label,
  the person's, or the generated one), never the `label` hook's, and keeps only its letters and
  digits, case-folded, so `~NatAda` matches `NatAda`. The text is the message as a text-only network
  shows it (`[photo] caption`), Unicode-normalized (NFKC), case-folded, with runs of whitespace made
  one space. A message cut short on the way (§6.3) does not match its original;
- `ctx.seen(fingerprint, within=…)` for scripts that want their own duplicate rules: whether another
  message with that fingerprint arrived within that time before this one;
- a test kit, `chatko.routing_api.testing`: the admin writes plain `pytest` checks next to
  `routing.py` (`config/test_routing.py`, which does `import routing`). A `FakeInstallation` is
  described as in `chatko.yaml` (extension instances, groups and their sites, sources, recipients,
  people), makes messages, fills the history (`hear`, `see`) and routes a message with the script
  as the hub would: it checks the script like the hub, then labels each target with its own
  label, the `label` hook or `default_label`. The result lists the targets by endpoint name with
  their label, text, recipients and the text as a network shows it (`NatAda: [photo] caption`);
  `assert_routed_to(result, "family.telegram", …)` checks where it went. It shows what the script asked
  for, before the invariants of §9.3, and raises the script's errors so that a test shows them.
  [`tests/examples/test_routing_example.py`](../tests/examples/test_routing_example.py) tests
  `routing.example.py` this way and is the template for the admin's tests;
- a version: the script may declare the version of `chatko.routing_api` it was written for
  (`api_version = (1, 0)`), so that after an upgrade that breaks it the hub runs the defaults and
  says why, instead of failing on every message.

The API itself is described in [architecture.md §4](architecture.md#4-routing-api).

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

Both are UTF-8 text. If a new version is invalid, the hub keeps the previous one and reports the
error as an admin notice.
A valid new version applies while the hub runs: an extension instance whose own section changed is
restarted (so is one that failed to start), one whose endpoints changed gets the new set without a
restart, new instances start and removed ones stop, after the deliveries in progress to them end.
A new routing script takes effect in the same moment as the new config it comes with.
`chatko run` runs the hub until SIGTERM or Ctrl+C; `chatko check-config` checks both files (and runs the routing script's tests) without starting the hub.
Beside the sections of the example, the config has `fingerprint_dedup_s` (§9.3: the window per endpoint,
in seconds, no longer than the retention, since fingerprints are kept only that long) and
`retention_days` (at most 3650). Admin notices go through the outbox like any message; notices
with the same cause are rate-limited together, and the next one says how many were held back.
While a new config is being applied, notices still go to the admin endpoint of the one in effect,
and a refused routing script that comes with the new config is reported to the new one.
Secrets (tokens, passwords, PSKs, the hub node's private key) come from environment variables
(`${VAR}` in YAML, values in `.env`), never from the files.

The hub never writes these files. SQLite (`data/chatko.sqlite3`) holds only runtime state: messages, the outbox, transport ids
and fingerprints for de-duplication, when nodes were last heard, accounts already seen. Old messages with nothing left to deliver and
old fingerprints are pruned after the retention (7 days by default).

## 11. Development and deployment

- **Development (now):** everything on the owner's computer. `docker compose` with a local Mosquitto,
  the hub's `meshtasticd`, and a second `meshtasticd` that plays a person's radio, so both `channel` and
  `dm` endpoints can be tested without hardware. When a hardware node is available, it is tested as a
  person's radio, then as our gateway (§6.2), and later as a physical hub node. `briar-headless` is
  built locally. A read-only connection to the Kyiv broker (`lab/spike_kyiv.py`) waits for a claimed
  physical node (D27). The Meshtastic lab is in [`lab/`](../lab/README.md) (image
  `meshtastic/meshtasticd`, pinned tag, amd64 and arm64). Its Mosquitto is set up like production's:
  no anonymous clients, a user per node, an ACL that keeps each to the lab's root topic (D46).
  Tests run the hub against it, with Telegram faked and the lab's second node as a member's radio,
  on demand and nightly in CI (D50).
- **Production:** a Linux server (a VPS, x86-64 or ARM64) running `deploy/docker-compose.yml`
  (the guide is [deployment.md](deployment.md)): the hub itself in a container too, as the host's user and
  with `config/` read-only, given only the secrets its config names; our Mosquitto (§6.2, D30); the
  hub's `meshtasticd` and `briar-headless`. Every container restarts after a crash or a reboot, and
  the logs rotate. Inbound ports: SSH, and MQTT over TLS (8883) for our gateways. Mosquitto
  allows no anonymous clients: each gateway and each hub node has its own user, limited by an ACL to
  the root topic. Gateway firmware does not check the broker's certificate (`setInsecure`), so TLS
  hides the passwords from eavesdroppers but not from an active attacker; the packets themselves are
  encrypted with channel PSKs or PKI anyway. The hub's nodes reach Mosquitto inside the Docker
  network. Telegram long polling and Tor are outbound. The `meshtasticd` TCP API (4403) has no
  authentication, so it stays inside the Docker network. `meshtasticd` containers need a restart
  policy, because a node reboot ends the process.
- Volumes: `config/` (chatko.yaml, routing.py, meshtasticd configs) and `data/` (SQLite, Briar and
  meshtasticd state: settings, the node database with members' keys, and the node's own key unless
  `private_key` is set). Secrets live in `.env`; `setup.sh` makes them, among them `private_key`, so
  that a rebuilt node keeps its identity. **Backups (D57):** once a day `deploy/backup.sh` archives
  `.env`, `config/` and `data/` and keeps the newest 14. The hub and `briar-headless` stop for the
  copy (a few seconds), because SQLite in WAL mode and Briar's database cannot be copied while open;
  both catch up on what they missed (the outbox, Briar's read flag). `meshtasticd` is copied as it
  runs (its files are written atomically, and the hub provisions it again if one is stale).
  Mosquitto keeps no state worth saving. The archive holds every secret, so it stays private on
  the server (mode 0600) and is copied off the server encrypted, by the operator (a lost server must
  not take its backups along). `deploy/restore.sh` unpacks an archive with the original owners,
  after moving what is there to `replaced-<time>/`.
- `briar-headless` asks for its account password on every start, because the password encrypts the
  database key. Its container entrypoint gives it `BRIAR_PASSWORD` (and the nickname on the first
  start) and writes `BRIAR_AUTH_TOKEN` as the API token, so it starts unattended (D28). The image
  (`deploy/briar/`, D53) is built from our fork for amd64 and arm64; its entrypoint gives the data
  directory to an unprivileged user and keeps the secrets out of Java's environment, and a health
  check asks the API. Its API (port 7000) stays on localhost or inside the Docker network.
- `docker stop` kills `meshtasticd` after 10 s (it does not exit on `SIGTERM`) without saving, so
  whatever the node learned but has not saved yet is lost (learned keys are saved at most once a
  minute); the hub keeps what it needs as favorites or in its own state (§6.2).
- Hosting: the owner's VPS, which is also the development machine; production is in `/opt/chatko`
  and the development checkout is kept apart (deployment.md §8).

## 12. Plan

The phases are split into working sessions in [roadmap.md](roadmap.md).

| Phase | What | Done when |
|---|---|---|
| 0. Spikes | S1 Briar relay with 3 phones. S2 local Meshtastic lab. S3 briar-headless build and API. See [spikes.md](spikes.md) | Every (verify) that affects v1 has an answer |
| 1. Core | endpoints, groups, people, labels, routing engine and routing API, outbox, admin notices, config and routing script, SQLite; extension API and the contract test suite; a fake extension | the core is fully tested with fake extensions and a sample routing script |
| 2. Telegram | group and private-chat endpoints, allowed chats | two Telegram groups work independently; foreign groups are ignored and reported |
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
