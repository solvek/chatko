# Spikes (phase 0)

Short experiments that answer the **(verify)** items of the design before we write production code.
Record the results in the "Result" section of each spike, with the date, versions and evidence (logs,
screenshots). Then update [design.md](design.md) and remove the answered **(verify)** marks.

## S1. Briar private-group relay (3 Android phones)

**Question:** can a private-group message reach a member who is never online, through another
member who met the hub-side device and then the offline member over Bluetooth/Wi-Fi?

Setup: Briar from the official source (F-Droid/Play/briarproject.org) on phones **H** (plays the hub,
creator of the group), **B** (courier) and **A** (offline member). Note the Briar version.

Steps:
1. H adds B and A as contacts, and A adds B (in person, "Add contact nearby").
2. H creates the private group "test" and invites B and A; both accept.
3. A and B reveal their relationship in the group ("Reveal contacts"). Also test the case where only
   **one** of them reveals.
4. Turn A into airplane mode with Bluetooth on (no internet, no Wi-Fi). Move A away from H (or turn
   H's Bluetooth off), so A cannot reach H directly.
5. H posts a message in the group. B syncs with H (both online, or nearby).
6. B takes internet off, comes close to A. Wait and watch whether A receives the message. Measure the
   time.
7. The reverse: A posts in the group while offline. B carries it to H.
8. Removal: can H remove A from the group? If not, what happens after H deletes A as a contact? Does A
   still get new messages through B?
9. Battery: does sync happen with the screen off, with and without battery optimization disabled?

Answers needed:
- [x] Relay works H → B → A and A → B → H.
- [x] One-sided reveal is enough / both sides must reveal.
- [x] A member can / cannot be removed; the effect of deleting the contact.
- [x] Background behaviour and time until delivery.

**Result (2026-09-30, Briar 1.5.20):**
- Relay works in both directions, H → B → A and A → B → H, while A never has internet.
- Without "Reveal contacts" members sync the group only with the creator, even when they are connected
  to each other. A reveal by **one** of the two members is enough. The item is in the group's ⋮ menu of
  a member; the creator does not have it (and does not need it).
- With the screens off and battery "Unrestricted", a message crossed B → A over Bluetooth in 1–2 min.
- A member **cannot be removed** from a private group (the creator has only Invite, Member list and
  Dissolve). Deleting the contact does not cut the member off: they still read and post through other
  members, and the creator still receives their posts.
- Dissolving the group stops it for every member who learns about it. A member who can only reach the
  creator through others does not learn about it and can still post, but nobody receives the posts.
- The invitation is a private message between the creator and the invitee, so the invitee needs a
  direct connection to the creator (internet) once to join.

Details:

Setup: Briar 1.5.20 from Google Play on all phones; battery set to "Unrestricted" for Briar on all.

| Role | Briar nickname | Device |
|---|---|---|
| H (hub) | Sergi | Samsung Galaxy A33 |
| B (courier) | Solvek Tablet | Xiaomi Tab 7 |
| A (offline member) | Patriotiv17 | Samsung Galaxy A12 |

Log:
1. Contacts. H had B and A as contacts already (added remotely by link). A and B added each other with
   "Add contact nearby" (QR); it worked. A has no internet; its contact H shows as offline.
2. Group. A took internet on for the invitation (an invitation is a private message between H and the
   invitee and cannot be relayed). H created private group `test` and invited B and A; both accepted.
   H's post `hello 1` reached A almost at once and B in about 30 s. The member list shows all three
   members on each device; the info banner says only the creator can invite.
   Note: A had no internet, so it got `hello 1` from H directly over Bluetooth (the phones lay close).
3. Relay **without any reveal** (the owner did not find "Reveal contacts" yet). H and B Bluetooth off,
   H posted `hello 2`, B got it over the internet, then B went offline with Bluetooth on next to A.
   B showed as online at A (the A–B contact connection was up), but `hello 2` **did not reach A**.
   This matches the design: without a revealed relationship, members sync the group only with the
   creator.
4. **One-sided reveal.** "Reveal contacts" is in the group's ⋮ menu of a non-creator member (not in
   the member list). A revealed B; B revealed nothing. `hello 2` appeared on A **at once**, carried by
   B over Bluetooth. Relay H → B → A works, and a reveal by one side is enough.
5. **Reverse relay.** A (offline) posted `hello 3`; B got it over Bluetooth, then B switched Bluetooth
   off and internet on. H (Bluetooth off) got `hello 3` almost at once. Relay A → B → H works.
6. **Screen off.** H posted `hello 4`, B got it over the internet, then B went Bluetooth-only and both
   B and A had their screens off next to each other. `hello 4` reached A in about 1–2 minutes with the
   screens off (battery "Unrestricted" on both). Not tested with battery optimization on.
7. **Removal.** The creator's group ⋮ menu has only "Invite", "Member list" and "Dissolve". There is
   **no way to remove a member** from a private group.
8. **Deleting the contact.** H deleted A as a contact, then posted `hello 5`. B carried it to A
   quickly, and A's `hello 6` reached H through B quickly as well. The member lists on H and A did not
   change. Deleting the contact does **not** cut a member off: A still reads and posts through other
   members, and H still receives A's posts.
9. **Dissolving.** H dissolved `test`. B (online) shows "the group was dissolved" with a button to
   remove it, and cannot post any more. A (no contact with H any more, reachable only through B) still
   can post, but nobody receives its posts: B no longer syncs the dissolved group. Dissolving is an
   effective cut-off.

## S2. Local Meshtastic lab (no hardware)

**Question:** does `meshtasticd` with a simulated radio work as a virtual node over MQTT, and what does
the Python API expose?

Setup: `docker compose` with Mosquitto and two `meshtasticd` containers without a radio. **hub**
plays chatko's virtual node; **radio** plays a member's node. Both connect to the local broker with
uplink and downlink enabled on the private channel.

Steps and answers needed:
- [x] The Docker image and the config needed for a simulated radio; the `linux/amd64` and
  `linux/arm64` images exist.
- [x] A private-channel message radio → hub arrives through MQTT and is visible via `TCPInterface`
  (portnum, from, channel index, packet id); hub → radio in the opposite direction.
- [x] A PKI direct message radio → hub and hub → radio. Does the Python API expose `pki_encrypted`
  and the sender's `public_key` on received packets?
- [x] How the two nodes learn each other's public keys over MQTT (NodeInfo on the primary channel),
  and how long it takes after a restart. Can the hub request NodeInfo from a member's node?
- [x] ACKs for direct messages sent through MQTT: does the hub get them?
- [x] De-duplication fields: are `(from, id)` stable across gateways?
- [x] The node identity (node id, keys) persists across container restarts (volume).
- [x] Setting channels, PSKs, names and MQTT settings from code or config files, so the hub can
  provision its node.
- [ ] (part 3: blocked on credentials, see the result; not needed for v1, D30) Read-only connection to the Kyiv broker: `LongFast` text messages are received. Broker policy
  for PKI direct messages (topic `…/2/e/PKI/…`) and for downlink (ask the Kyiv community). Do the Kyiv
  gateways downlink PKI direct messages to nodes they hear? This decides whether `dm` mode works
  without our own gateway.
- [ ] Later, with hardware: the same tests with a physical node over USB/TCP/BLE as the hub's node.

**Result, part 1 (2026-09-30, session S02; `meshtasticd` 2.7.26.54e0d8d, `meshtastic` Python 2.7.11,
Mosquitto 2.1.2):**
- The lab is in [`lab/`](../lab/README.md): `docker compose` with Mosquitto and two `meshtasticd`
  containers, `provision.py` and `spike_channel.py`. From an empty state to a passing channel test:
  `up`, `provision.py`, `spike_channel.py`.
- Private-channel text works radio → hub and hub → radio through MQTT, and the receiver sees it on
  `TCPInterface` with the sender's `from`, the sender's packet `id` and the local channel index. Local
  latency is well under a second.
- **Firmware pitfall:** setting a duty-cycle region such as `EU_868` for the first time turns
  `lora.ignore_mqtt` on, and a node with it on drops and does not relay any packet that crossed MQTT.
  This also concerns members' radios and relays on a real mesh (design.md §6.2).
- `meshtasticd` serves **one** API client at a time. Admin messages sent back to back were partly lost.
- Answered here as well: provisioning names, region, channels, PSKs and MQTT from code works over the
  admin API; the `meshtasticd` YAML covers only the hardware, the MAC address (node id) and logging.

Details:

*Image.* `meshtastic/meshtasticd` on Docker Hub. The pinned tag `2.7.26-beta` (the same digest as
`latest` and `beta` on 2026-09-30) is a multi-arch index with `linux/amd64`, `linux/arm64`,
`linux/arm/v7` and `linux/riscv64` (Debian based; `-alpine` tags exist too). `daily` tags carry 2.8.x
builds. Only amd64 was run; arm64 was checked in the manifest (`docker buildx imagetools inspect`).
The image runs `meshtasticd --fsdir=/var/lib/meshtasticd` (a volume) and exposes 4403 (API) and 9443
(web UI, not used).

*Config for a simulated radio* (`/etc/meshtasticd/config.yaml`, read from `firmware/src/platform/
portduino/PortduinoGlue.cpp`):

```yaml
Lora:
  Module: sim                  # SimRadio: no LoRa hardware (same as the --sim flag)
General:
  MACAddress: 02:00:C4:A7:B0:01 # required in Docker; node id = last 4 bytes -> !c4a7b001
Logging:
  LogLevel: info               # debug shows why packets are dropped
```

Without `MACAddress` (or `MACAddressSource`) the daemon exits with "Blank MAC Address not allowed!"
on Linux without Bluetooth. SimRadio does not transmit anywhere (it only reports packets to the API
client for the Meshtasticator simulator), and `Config: EnableUDP` is off by default, so two containers
talk only through MQTT. "Portduino critical error: Failed to open /dev/spidev0.0" at start is
harmless. The YAML has no keys for channels, MQTT, names or region: those live in the node's prefs in
the data volume and are set through admin messages.

*Provisioning* (`lab/provision.py`, over the TCP API with the Python library): `setOwner`, then in one
`beginSettingsTransaction`/`commitSettingsTransaction`: `lora` (region, `config_ok_to_mqtt`,
`ignore_mqtt`), `mqtt` (enabled, address, root, encryption on, JSON off, TLS off, empty user and
password: the defaults are the public `mqtt.meshtastic.org` with `meshdev`/`large4cats`), channel 0
(uplink and downlink on) and channel 1 (`SECONDARY`, name, 32-byte PSK, uplink and downlink). Findings:
- Sent back to back, only some admin messages were handled: owner, lora, mqtt and channel 0 never
  showed up in the node's log, channel 1 and the commit did. With a 1 s pause between messages all
  were handled. The cause was not traced; the S19 adapter should send admin messages one at a time and
  wait for each response.
- `setOwner` and the commit of settings that need it reboot the node about 7 s later. In
  `meshtasticd` a reboot **ends the process**; Docker's restart policy (`restart: unless-stopped`)
  starts it again (the container's restart count went up by one per provisioning). Without a restart
  policy the node stays down. The Python client must reconnect.
- `AdminModule.cpp`: when the region goes from `UNSET` to a region with a duty-cycle limit, the firmware
  sets `ignore_mqtt = true`, overriding the value in the same message. A second `set_config.lora` with
  `ignore_mqtt = false` sticks. The same code also appends the region to the MQTT root only if the
  root is still the default `msh`.
- `meshtasticd` accepts one API client: a new connection logs "Force close previous TCP connection" and
  closes the old one. Closing a `TCPInterface` right after it connects races with the library's first
  heartbeat, which then reconnects by itself (a broken-pipe traceback and a second connection).

*Why nothing arrived at first.* The radio's packet reached the hub's MQTT client ("Received MQTT topic
msh/lab/2/e/Family/!c4a7b002"), was decrypted with channel 1, and was then dropped by
`Router::perhapsHandleReceived` (debug log "Msg came in via MQTT from 0xc4a7b002") because
`config.lora.ignore_mqtt` was on. This check runs before `handleReceived`, which is also where
flooding rebroadcasts, so such a node neither shows nor relays the packet. The via-MQTT flag travels in
the LoRa header (`PACKET_FLAGS_VIA_MQTT_MASK`), so the same applies to packets a gateway downlinks to
the air.

*MQTT path.* A node publishes each packet it sends on a channel with uplink on to
`<root>/2/e/<channel name>/<gateway id>`, e.g. `msh/lab/2/e/Family/!c4a7b002`, as a `ServiceEnvelope`
(`channel_id` = the channel **name**, `gateway_id` = the publishing node). It subscribes to
`<root>/2/e/<name>/+` for each channel with downlink on, plus `<root>/2/e/PKI/+`. A received envelope is
accepted only if its `channel_id` matches a local channel name with downlink on, and is decrypted with
that channel's key; the packet itself carries only an 8-bit channel hash. So a private channel must have
the **same name and PSK** on every node. A node ignores envelopes it published itself and turns its own
packet coming back into an implicit ACK.

*Packet fields* (`spike_channel.py`, one run each way):

| Where | Field | radio → hub | Notes |
|---|---|---|---|
| MQTT envelope | topic | `msh/lab/2/e/Family/!c4a7b002` | |
| | `channel_id`, `gateway_id` | `Family`, `!c4a7b002` | |
| | `packet.from`, `to`, `id` | `!c4a7b002`, `0xffffffff`, `0x0973632b` | the same id as `sendText` returned |
| | `packet.channel` | `117` (`0x75`) | channel hash, not an index |
| | `hop_limit`, `hop_start`, `want_ack`, `pki_encrypted` | 3, 3, false, false | |
| | payload | `encrypted`, 28 bytes for 22 characters | |
| receiver (`meshtastic.receive.text`) | `from`, `to`, `id` | `3299323906`, `4294967295`, `158556971` | integers; `toId = "^all"` |
| | `fromId` | `None` | set only once the sender's NodeInfo is known |
| | `channel` | `1` | the **local** channel index |
| | `hopLimit`, `hopStart` | 3, 3 | |
| | `viaMqtt`, `transportMechanism` | `True`, `TRANSPORT_MQTT` | |
| | `decoded.portnum`, `decoded.text` | `TEXT_MESSAGE_APP`, the text | |
| | `rxTime`, `rxSnr`, `rxRssi` | absent | the node has no network time; use the hub's clock |

hub → radio gave the same picture with the roles swapped (`from = 3299323905 = !c4a7b001`).

*Names.* The hub had not heard the radio's NodeInfo by the end of the run: its node list showed
`Meshtastic b002`, hardware `UNSET`, no public key. That name is a placeholder the Python library makes
for unknown nodes, not the node's name, so the extension must not use it as an author name. How and when
NodeInfo is learned is part 2 (S03).

Left for S04: the Kyiv broker. The `ignore_mqtt` default on Kyiv radios and relays is a question for
the Kyiv community (S04).

**Result, part 2 (2026-09-30, session S03; the same versions):**
- PKI direct messages work radio → hub and hub → radio through MQTT (topic
  `<root>/2/e/PKI/<gateway>`). The receiver's packet has `pkiEncrypted = True` and `publicKey` = the
  sender's key (base64), plus `from`, `id`, `wantAck` and `viaMqtt`. A text direct message needs a
  known key on **both** sides: the sender refuses to send without the receiver's key (NAK
  `PKI_SEND_FAIL_PUBLIC_KEY`), and a receiver drops old channel-encrypted text DMs ("Rejecting legacy
  DM").
- ACKs come back through MQTT. The sender's API client gets two ACKs: an **implicit** one from its
  own node as soon as the broker echoes the packet back (it only means "the broker has it"), and the
  real one from the destination (`from` = the destination node). Without the real ACK the firmware
  publishes the packet twice more (about 7.5 s apart) and gives up with NAK `MAX_RETRANSMIT` after
  about 23 s. Other NAKs: `PKI_UNKNOWN_PUBKEY` (the receiver does not know our key) and `NO_CHANNEL`
  (the receiver cannot decrypt, e.g. it has another key for us).
- `(from, id)` is stable: other gateways publish the same packet with their own `gateway_id`, topic
  and a lower `hop_limit`, and the hub's node hands it to the API client once. The sender picks the
  id: the firmware for radio packets, the Python library for the hub's (22 random bits and a 10-bit
  counter), so ids are not unique forever; de-duplicate within a time window.
- Keys travel in NodeInfo on the primary channel: each node broadcasts it 30 s after boot and every
  3 h. A node that knows a key keeps it (**key pinning**): a NodeInfo with another key is dropped.
  Other ways a key arrives: a node that cannot decrypt a direct message answers with NAK
  `PKI_UNKNOWN_PUBKEY`, and the sender's firmware then sends its NodeInfo to it at once; the admin
  message `add_contact` stores a node with its key (overriding a pinned one) as a favorite. The hub
  **can** ask a node for its NodeInfo, but the node answers only if it has not sent NodeInfo for
  10 minutes (longer on a busy mesh) and has not answered this hub within 12 hours.
- Identity: the node id comes from `MACAddress` in the YAML; the key pair lives in the volume and
  survives restarts and re-created containers. It can be set through the admin API
  (`security.private_key`; the firmware derives the public key), which restored the hub's identity
  after its volume was wiped: members' pinned keys still matched. Keys **learned** from NodeInfo are
  written to disk at most once a minute, and not at all in the first minute after start, and
  `docker stop` kills `meshtasticd` after 10 s without saving: keys learned at boot were lost on
  every restart. Keys stored with `add_contact` are saved at once.
- The lost admin messages of part 1 are explained: the node queues packets for itself in a queue of 4
  and drops the oldest when it is full. Waiting for each admin message's response (under 50 ms)
  replaces the 1 s pause; a fresh node was provisioned in 2.2 s instead of about 10 s.
- The node drops a text from its API client that comes less than 2 s after the previous one. The
  NAK meant for the client (`RATE_LIMIT_EXCEEDED`) is addressed to node 0 and goes out to MQTT
  instead, so the client sees nothing (a firmware bug worth reporting upstream).

Details:

*Scripts* (`lab/`, all through `labkit.py`): `spike_dm.py` (keys, direct messages both ways, the rate
limit, duplicates from made-up gateways; `--offline` stops the radio), `spike_keys.py` (`show`,
`learn`, `contact`), `spike_admin.py` (admin messages back to back versus one at a time).
`provision.py` now waits for each admin response, sets lab private keys and exchanges contacts.

*Direct message, one run each way* (`spike_dm.py`):

| Where | Field | radio → hub | Notes |
|---|---|---|---|
| MQTT envelope | topic, `channel_id` | `msh/lab/2/e/PKI/!c4a7b002`, `PKI` | the sender publishes; no channel name |
| | `packet.channel` | `0` | no channel hash for PKI |
| | `want_ack`, `pki_encrypted` | true, true | |
| | payload | `encrypted`, 43 bytes for 25 characters | 12 bytes more than a channel packet |
| receiver | `pkiEncrypted`, `publicKey` | `True`, the radio's key | |
| | `fromId` | `!c4a7b002` | the key was known, so the node was too |
| | `wantAck`, `viaMqtt`, `transportMechanism` | `True`, `True`, `TRANSPORT_MQTT` | |
| | `channel` | absent (0) | |
| | latency | 0.15 s (hub → radio 0.25 s) | |
| sender | ACK/NAK | implicit ACK (`from` = own node) and ACK from the destination, both within 0.3 s | |

The ACK is a routing packet on the primary channel, e.g. `msh/lab/2/e/LongFast/!c4a7b001` from the
hub to the radio, 13 bytes; for a text DM it asks for an ACK itself, and the original sender answers
with a hop-limit-0 ACK. So one direct message is three packets on MQTT, and the primary channel
needs uplink **and** downlink on both nodes for ACKs to flow.

*Radio offline* (`spike_dm.py --offline`, the radio container stopped): the hub published the packet
at +0.2 s, +7.8 s and +15.3 s, got an implicit ACK after each, and NAK `MAX_RETRANSMIT` (from its
own node) at +23.0 s. Implicit ACKs from MQTT do not stop the retransmissions
(`ReliableRouter::sniffReceived`). Nothing is delivered when the radio comes back.

*Rate limit* (`spike_dm.py`): of two direct texts 0.5 s apart, the second got no ACK, NAK or MQTT
publication. The hub's log: "Rate limit portnum 1", "Alloc an err=38,to=0x0", "Packet received
with to: of 0!", and a routing packet to `0x0` appeared on `LongFast`
(`PhoneAPI::handleToRadioPacket` calls `sendRoutingErrorResponse` with the client packet's `from`,
which is 0). Positions, waypoints, alerts and telemetry from the client are limited to one per
10 s and dropped without any NAK.

*Duplicates* (`spike_dm.py`): the radio's DM was published again under the made-up gateways
`!c4a7b0f1` (hop limit 2) and `!c4a7b0f2` (hop limit 1). The hub's API client saw the packet once,
with the original hop limit 3.

*Key learning* (`spike_keys.py learn`, after the nodes had exchanged NodeInfo):

| Step | What the hub's client got |
|---|---|
| the hub forgets the radio (`remove_by_nodenum`), sends a DM | NAK `PKI_SEND_FAIL_PUBLIC_KEY` from its own node, 0.1 s |
| the hub sends its NodeInfo to the radio with `want_response` | no answer: the radio had sent NodeInfo < 10 min before; its log shows "Update Node Pubkey" for the hub, then nothing. In a second run, 10 min after the radio's last NodeInfo: the answer came at once, and the next DM got an ACK |
| the hub adds the radio's key with `add_contact`, sends a DM | ACK from the radio |
| the radio forgets the hub; the hub sends a DM | NAK `PKI_UNKNOWN_PUBKEY` from the radio, 0.3 s; the hub's firmware sent its NodeInfo to the radio right after ("PKI decrypt failure, send a NodeInfo") |
| the hub sends the DM again | ACK from the radio |

The throttles, from the firmware: a node sends its own NodeInfo at most once per 10 minutes, scaled
up with the number of nodes online (`NodeInfoModule::allocReply`), and remembers this across restarts
(`prefs/transmit_history.dat`): after a quick restart the 30 s boot broadcast is skipped. A reply to
a NodeInfo request is also suppressed if the node answered the same requester within 12 hours
(kept in memory). The NodeInfo sent after `PKI_UNKNOWN_PUBKEY` uses a 60 s throttle instead. A node
that hears a decoded packet from an unknown node sends that node its NodeInfo and asks for a reply
("Heard new node on ch. N"), unless its node database is full. A heartbeat with `nonce = 1` from the
API client makes the node broadcast its NodeInfo (asking for replies) on the 60 s path.

*Persistence* (`docker compose restart`, then `spike_keys.py show`): node ids and own keys stayed the
same. The peers' keys, learned at +30 s, were gone after every restart: `NodeDB::updateUser` saves
only if the last save was more than a minute ago, and `lastNodeDbSave` starts at 0, so nothing
learned in the first minute is written until some later save. A key learned at 80 s of uptime, and
a contact added with `add_contact`, survived. `docker stop` took 10 s and ended with exit code 137:
`meshtasticd` runs as PID 1 and does not exit on `SIGTERM`, so it is killed without saving.

*A member's radio with a new key* (the radio's volume wiped and the radio provisioned again; the hub
still had the old key as a favorite): a fresh node has **no** key pair until a region is set ("Generate
new PKI keys"). Then: radio → hub NAK `PKI_SEND_FAIL_PUBLIC_KEY` (the fresh radio knew nothing); hub
→ radio first `PKI_UNKNOWN_PUBKEY` (and the hub's NodeInfo taught the radio the hub's key), then
`NO_CHANNEL` (radio's log: "PKC decrypt attempted but failed!"). The radio's NodeInfo with the new key
was dropped by the hub ("Public Key mismatch, dropping NodeInfo", only in the node's log), **but** the
hub's API client still received that NodeInfo, and the library's `nodesByNum` then showed the new key
although the node kept the old one: the library's node cache is not the node's database.
`add_contact` with the new key fixed it at once.

*The hub's volume lost* (the hub's key set from the lab config, the radio holding it as a favorite):
after `provision.py --only hub --no-contacts` on the wiped hub, the hub had the same public key. The
radio's first DM got NAK `PKI_UNKNOWN_PUBKEY` from the hub (its node database was empty), the radio's
firmware sent its NodeInfo, and from then on DMs worked both ways with no action on the radio.

*Admin messages* (`spike_admin.py`, `Logging: LogLevel: debug` on the hub): 10 `get_config`
requests back to back got 5 responses, and the log had 5 lines "fromRadioQ full, drop oldest!"
(`Router::enqueueReceivedMessage`, `MAX_RX_FROMRADIO = 4`). One at a time, waiting for each
response: 10 of 10, 9–47 ms each over two runs. Every admin message with `want_response` gets a
response (a routing packet with `NONE`, or data); `commit_edit_settings` always reboots the node 7 s
later.

*Firmware facts relevant to a real mesh* (read in `MQTT.cpp`, `Router.cpp`, `NodeDB.cpp`; to be
checked on the Kyiv mesh in S04 and S23):
- A gateway uploads a packet from another node that it can decode (channel texts, ACKs, NodeInfo)
  to a broker on a public address only if the sender has "OK to MQTT" (`lora.config_ok_to_mqtt`)
  on, which is **off** by default. PKI direct messages it cannot decode are uploaded regardless.
- Relays forward packets they cannot decode (other channels, other nodes' DMs) in the rebroadcast
  modes `ALL` (the default) and `CORE_PORTNUMS_ONLY` (the default of `ROUTER`). `LOCAL_ONLY` (the
  default of `CLIENT_HIDDEN`) and `KNOWN_ONLY` drop them, except DMs whose sender or receiver they
  know (`RoutingModule::handleReceivedProtobuf`).
- A gateway downlinks a PKI packet only if one of its channels has downlink on, and only if the
  packet is addressed to it or it knows **both** the sender and the receiver in its node database.
  The hub's NodeInfo reaches a gateway only through MQTT downlink on the primary channel.
- When the node database is full (`MaxNodes`, 200 in the lab YAML), the oldest nodes without a key
  are evicted first, then the oldest others; favorites (every `add_contact`) and ignored nodes never.
- A virtual node has no clock: `lastHeard` and `rxTime` are absent. The hub uses its own clock.

**Result, part 3 (2026-09-30, session S04; Kyiv broker, read-only):**
- *What the website <https://meshtastic.kyiv.ua/join> tells.* The QR code is a channel URL
  (<https://meshtastic.org/e/#CjQSIFziz2R01sx4MpCcWd6Z49dJjCXa_IJYG5bDRi1CL3dcGghMb25nRmFzdCgBMAE6AgggCjISIHJheFM1Vm52VkNMcWZRcmVwUm9sYWh0TUpCNWxYWm81GgZLeWl2VUEoATABOgIIIBIOCAE4DkAFSAFQClgBaAE>);
  decoded, it holds: region **EU_433** per the website (433.125 MHz, not `EU_868`; the URL's region field is enum 14, which the library names `UA_433`, a band-identical code; the owner chose `EU_433` for the node and the lab on 2026-10-05), preset `LONG_FAST`, hop limit 5,
  TX power 10 dBm, and two channels: channel 0 **`LongFast`** with a **non-default 32-byte PSK**
  (public, in the URL), and a secondary channel **`KyivUA`** with its own 32-byte PSK. Members are told
  to use the secondary channel for chat. The website states the broker host in its page config:
  `mqtt.meshtastic.kyiv.ua` (157.180.74.120; ports 1883 and 8883 accept TCP), and its statistics
  show mostly text messages and no private ones. Nothing is published about the root topic, the
  credentials, PKI topics, downlink or gateway firmware.
- *Connection.* An anonymous connection to port 1883 is refused (`CONNACK` 4, bad user name or
  password), so a read-only client needs credentials from the community. `lab/spike_kyiv.py` is the
  listener: it subscribes to a filter, never publishes, and prints topic prefixes, channels, ports and
  the `pki_encrypted` and `via_mqtt` flags of the envelopes. Credentials come from `KYIV_MQTT_USER` and
  `KYIV_MQTT_PASSWORD` (or `--user`, `--password`), and `--tls` uses port 8883.
- *Getting credentials (from the community chat).* Register on the website with Telegram, verify a node,
  mark in its settings that it uses MQTT, and the website generates a login, password and host. The website
  has no node-verification page, so the community has to be asked; the node probably has to be seen on
  the mesh first, which a virtual node cannot do.
- *How a node gets access (screenshots of a member's cabinet and node settings, 2026-10-01).* The
  cabinet lists nodes of the registry; a user **claims** a node ("Ваша нода") and states its type,
  antenna, place, and "MQTT: yes". Then the cabinet shows server `mqtt.meshtastic.kyiv.ua`, a login
  that is the **node id** (hex, no `!`), a password, and the topic **`node/<node id>`**. In the node's
  settings: MQTT address and credentials as shown, root topic `node/<node id>` (**not** `msh/EU_433`),
  TLS **off** (port 1883), encryption on ("send encrypted packets"), JSON off, map reporting on. A
  checkbox "enable routing" lets "remote nodes join the local network" (the website warns that MQTT
  traffic may be large). So access is per node, tied to a node the registry has seen, and each node
  publishes under its own root. A virtual node that never transmits is not in the registry, so it
  cannot be claimed: the community's answer was to wait for a physical node to be confirmed (and even
  that is not guaranteed). It is not clear what "routing" does with packets published under
  `node/<id>` (whether the broker bridges them to other nodes' topics), nor whether a client may read
  other nodes' topics.
- *Consequences.* (1) The "public default-key `LongFast`" of the design does not exist on this mesh:
  reading `LongFast` needs its PSK, and the hub's primary channel must be that one, or its NodeInfo,
  ACKs and `dm` cannot work (design.md §6.2). (2) `EU_433` is a region with a duty-cycle limit too,
  so the firmware turns `ignore_mqtt` on there as well (confirmed in the source in S07, see the
  wrap-up below). (3) The example config
  uses `EU_433`, the `KYIV_PRIMARY_PSK` secret and the root topic `node/<hub node id>`, TLS off.
  (4) Without a claimed physical node the hub gets no access to the Kyiv broker, so development goes
  on with our own Mosquitto (lab, D27).
- *Open, questions for the Kyiv community (drafted in Ukrainian in the S04 chat for the owner to send):* credentials and root topic; PKI topic and downlink policy; gateway firmware; "Ignore MQTT"
  on relays; downlink of `LongFast`; how many radios have "OK to MQTT"; may a bot node join the mesh.

## S3. briar-headless build and API

**Question:** can we build and run `briar-headless` locally, and how big is the private-group patch?

Steps and answers needed:
- [x] Build `x86LinuxJar` (and `aarch64LinuxJar`) from the current upstream; the JDK version needed.
- [x] Run it in Docker with a persistent data volume and a non-interactive account creation.
- [x] Contacts API end to end with a phone: exchange links, `ContactAddedEvent`, private messages both
  ways over the WebSocket. Confirm that a contact at a distance needs **both** sides to add the other's
  link, and write down the exact `curl` calls the admin will use (D23).
- [x] Read `PrivateGroupManager`, `GroupInvitationManager` and how the Android app uses them. List the
  methods for the patch (§7.4 of the design): create, list, members, invite, dissolve, invitations from
  others, read and post (D24).
- [x] Does `briar-headless` include Briar's LAN (Wi-Fi) transport, so a hub without internet can sync
  with phones on the same network? Not needed for v1; it decides the future home hub (D17).
- [x] Upstream contribution rules for briar-headless (code style, tests, merge request process).

**Result, part 1 (2026-10-01, session S05; build, Docker, contacts and private messages):**
- *Build.* Upstream tag `release-1.5.21` (2026-09-27) builds with **JDK 17** (Temurin 17.0.20; the
  Gradle files set `jvmToolchain(17)` and `release = 17`) and the Gradle 8.14.3 wrapper.
  `./gradlew --configure-on-demand briar-headless:x86LinuxJar briar-headless:aarch64LinuxJar` needs no
  Android SDK and takes about 2 min in Docker with an empty Gradle cache. Each jar is 44 MB and
  carries Tor and lyrebird for its architecture. The aarch64 jar was built but not run (no ARM64 host
  or emulation here); the JVM part is the same, only the Tor binaries differ.
- *Docker.* `lab/briar/`: a two-stage Dockerfile (the JDK stage clones the tag and builds both jars on
  the build machine; the runtime stage on `eclipse-temurin:17-jre` takes the jar for `TARGETARCH`), an
  entrypoint and a compose file with the volume `briar-data` at `/data` and the API on
  `127.0.0.1:7000`. Image 339 MB; the running peer uses about 250 MB of RAM and 65 MB of data.
- *No terminal.* briar-headless reads the account from stdin: nickname, password and confirmation
  the first time, and **the password on every start** (the database key is encrypted with it). There
  is no option for a password file. The entrypoint feeds stdin from `BRIAR_PASSWORD` (and
  `BRIAR_NICKNAME` the first time) and decides by whether `/data/key/db.key` exists. A wrong
  password makes the process exit 1. The API token is the file `/data/auth_token`, read verbatim
  and made on first start if missing; the entrypoint writes `BRIAR_AUTH_TOKEN` into it, so the token
  can come from `.env`. Account creation, restart, `docker compose up --build` (a new container on
  the same volume) and the token from `.env` all work. The `briar://` link stays the same across
  restarts. A stop takes about 1 s (the shutdown hook stops Briar's services), and the API answers
  about 2 s after a start.
- *The API.* Javalin listens on all interfaces in the container, so the published port works. Every
  REST call needs `Authorization: Bearer <token>` (401 without). The WebSocket `/v1/ws` needs the
  token as its first message. JSON bodies need no `Content-Type` header.
- *Adding a contact at a distance needs both sides* (D23, design.md §7.1). The phone (Briar 1.5.20,
  "Sergi", Samsung A33) added the hub's link at about 10:05. For 4 minutes the hub saw nothing: no
  pending contact, no event. The phone showed "Connecting…": the app shows that for 15 s after each
  rendezvous poll (once a minute) even while it is only waiting, so it does not mean the other side
  was found. At 10:09:18 the hub added the phone's link. The WebSocket then showed
  `PendingContactAddedEvent` and `PendingContactStateChangedEvent` `waiting_for_connection`, then
  `adding_contact` (10:09:33), then `PendingContactRemovedEvent`, `ContactAddedEvent`
  (`verified: false`) and `ContactConnectedEvent` (10:09:35): **17 s** after the second side added
  its link. (In S1 the owner could not add contacts at a distance either, but did not look into it;
  here it worked once both sides had added the other's link.)
- *Verification.* A contact added at a distance is "unverified" on both sides, and Briar cannot make
  it verified later: only "Add contact nearby" (QR codes in person) creates verified contacts, and
  nothing in the app or briar-headless calls the database's `setContactVerified`. The hub's contacts
  are therefore always unverified. That does not affect syncing or messages.
- *Private messages both ways.* Phone → hub: `ConversationMessageReceivedEvent` on the WebSocket
  within a second, and the message in `GET /v1/messages/1`. Hub → phone: `POST /v1/messages/1`, then
  `MessagesSentEvent` at once and `MessagesAckedEvent` (the phone's two ticks) 1 s later. Right after the
  contact was added, the WebSocket also showed `MessagesSentEvent` and `MessagesAckedEvent` for
  messages that are not in the conversation (Briar's own sync between the two peers); a client
  should match message ids. After a restart the hub was connected to the phone again within
  about 25 s.
- *The admin's calls* (until `briarctl` exists, and what `briarctl` wraps). With `TOKEN` from `.env`:

  ```bash
  curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7000/v1/contacts/add/link
  curl -H "Authorization: Bearer $TOKEN" -d '{"link":"briar://…","alias":"Ada"}' http://127.0.0.1:7000/v1/contacts/add/pending
  curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7000/v1/contacts/add/pending
  curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7000/v1/contacts
  curl -H "Authorization: Bearer $TOKEN" -X PUT -d '{"alias":"Ada"}' http://127.0.0.1:7000/v1/contacts/1/alias
  ```

  The first prints the hub's link for the person, who adds it in the app ("＋", "Add contact at a
  distance"); the second adds the person's link (from the same screen of their app). A bad link gives
  400 `INVALID_LINK`; a link of an existing contact gives 403 `CONTACT_EXISTS` with the contact's
  name. The pending list shows the state until the contact appears in `/v1/contacts`.
- *Tooling.* `lab/spike_briar.py` (`link`, `add`, `pending`, `contacts`, `watch`, `send`, `messages`)
  is the spike client; `lab/README.md` has the steps.

**Result, part 2 (2026-10-01, session S06; private groups, LAN, upstream; read in the source of tag
`release-1.5.21` in `~/Projects/briar`, no phone):**
- *Private groups are switched off in briar-headless.* `HeadlessModule.kt` returns `false` from
  `FeatureFlags.shouldEnablePrivateGroupsInCore()`. With that, `PrivateGroupModule` and
  `GroupInvitationModule` still create the managers but register no message validators, no
  incoming-message hooks, no contact hook and no client versions. So the peer drops private-group
  messages, never tells its contacts that it has the private-group clients (a phone then shows it as
  "not supported" and cannot invite it), and creates no invitation sessions. The flag came with
  commit `707802c45` (2022), when headless had no API for private groups; the headless tests already
  run with it on (`TestFeatureFlagModule`). The patch must turn it on.
- *Turning it on for an existing account* needs no migration: `GroupInvitationManagerImpl
  .onDatabaseOpened` creates the client's local group and calls `addingContact` for every existing
  contact, and `ClientVersioningManagerImpl.startService` sends the new client list to all contacts
  when it changed. Confirmed in S24 (D51): a phone saw the hub as "not supported" while it ran
  upstream and could invite it once they were connected again after the switch, on the same data.
- *The model* (`briar-spec` clients "Private Group" and "Private Group Sharing"; `briar-core`
  `privategroup/`). A group is a name, a 32-byte salt and its creator; its id is the hash of these.
  **Only the creator can invite.** Each invitation runs as a session in the private conversation
  between the creator and a contact, and the session id is the group id. A member's first message in
  the group is a JOIN carrying the creator's signature from the invitation; a POST names the author's
  previous message and must be later than it, and Briar delivers a post only after that message, so one
  author's posts arrive in order. Everything is signed by its author. **Dissolving** is the creator
  removing the group (`removePrivateGroup`): a hook sends LEAVE in every invitation session, and a
  member marks the group dissolved (`GroupDissolvedEvent`) when that message arrives. LEAVE travels
  in the private conversation, which syncs only directly between the creator and that member, never
  through other members. A member who removes the group leaves it. Removing a contact who created a
  group marks that group dissolved (`GroupInvitationManagerImpl.removingContact`). A contact who
  joined and then left cannot be invited back into the same group: the creator's session stays in
  `LEFT`, which `getSharingStatus` reports as `SHARING`.
- *How the Android app does it* (`briar-android/.../privategroup/`); the patch makes the same calls:

  | Operation | App | Calls |
  |---|---|---|
  | create | `CreateGroupControllerImpl.createGroup` | `PrivateGroupFactory.createPrivateGroup(name, localAuthor)`, `GroupMessageFactory.createJoinMessage(groupId, now, localAuthor)`, `PrivateGroupManager.addPrivateGroup(group, join, true)` |
  | invite | `CreateGroupControllerImpl.sendInvitation` | `ConversationManager.getTimestampForOutgoingMessage(txn, c)`, `AutoDeleteManager.getAutoDeleteTimer(txn, c, timestamp)`, `GroupInvitationFactory.signInvitation(contact, groupId, timestamp, privateKey)`, `GroupInvitationManager.sendInvitation(groupId, c, text, timestamp, signature, timer)`; who can be invited: `getSharingStatus(contact, groupId)` |
  | list | `GroupListViewModel.loadGroups` | `getPrivateGroups`, `isDissolved`, `getGroupCount` |
  | members | `GroupMemberListControllerImpl.loadMembers` | `getMembers` |
  | read | `GroupViewModel.loadItems` | `getHeaders` (joins are `JoinMessageHeader`), `getMessageText`, `setReadFlag` |
  | post | `GroupViewModel.createAndStoreMessage` | `getPreviousMsgId`, `getGroupCount().latestMsgTime`, timestamp `max(now, latest + 1)`, `GroupMessageFactory.createGroupMessage(groupId, timestamp, parent, localAuthor, text, previousMsgId)`, `addLocalMessage` |
  | dissolve, leave | `GroupListViewModel.removeGroup` | `removePrivateGroup` |
  | invitations to us | `GroupInvitationControllerImpl` | `getInvitations` (group and creator contact), `respondToInvitation(contactId, group, accept)` |
  | reveal contacts | `RevealContactsControllerImpl.reveal` | `revealRelationship(contactId, groupId)` (not needed by the hub) |

- *Events.* `GroupMessageAddedEvent(groupId, header, text, local)` for every join (text `""`) and
  post, the hub's own (`local`) included; `GroupDissolvedEvent` when a remote creator dissolves;
  `GroupInvitationRequestReceivedEvent` and `GroupInvitationResponseReceivedEvent`, which are
  `ConversationMessageReceivedEvent`s that headless **already** sends to the WebSocket and lists in
  `GET /v1/messages/{contactId}` (`OutputEvent.kt`, `MessagingControllerImpl.JsonVisitor`); the
  request's `sessionId` is the group id. `ContactRelationshipRevealedEvent` is not needed.
- *Limits* (`PrivateGroupConstants`): name ≤ 100 bytes, post and invitation text ≤ 31 744 bytes of
  UTF-8 (32 KiB minus 1 KiB).
- *How briar-headless is built.* One package per feature (`contact/`, `messaging/`, `forums/`,
  `blogs/`): a `…Controller` interface, a `@Singleton` `…ControllerImpl` with an `@Inject`
  constructor, `Output….kt` extension functions that turn Briar objects into `JsonDict`, and a Dagger
  `Headless…Module` that binds the controller and registers it on the `EventBus` when it forwards
  events. `Router.kt` maps the routes under `/v1` (Javalin 3.5.0 on Jetty 9.4.20) behind the bearer
  token. Errors are `BadRequestResponse`/`NotFoundResponse`, or a status with `{"error": "CODE"}`
  (`CONTACT_EXISTS` with 403). Handlers call the managers on Javalin's threads; listeners send events
  through `WebSocketController`, which only reaches connected, authenticated sessions. Byte ids are
  standard base64 in JSON (Jackson), and the existing endpoints keep them out of URL paths (a pending
  contact id and a message id go in the body), because base64 can contain `/`.
- *The patch plan* (S24), all in `briar-headless`:
  - `HeadlessModule.kt`: `shouldEnablePrivateGroupsInCore() = true`; include the new module (also in
    the test `HeadlessTestModule.kt`).
  - `privategroups/`: `PrivateGroupController`, `PrivateGroupControllerImpl` (endpoints and events of
    design.md §7.4; it is an `EventListener` forwarding non-local `GroupMessageAddedEvent`s and
    `GroupDissolvedEvent`s), `OutputPrivateGroup.kt` (group, message header, member, sharing status,
    invitation item, events), `HeadlessPrivateGroupModule`.
  - `Router.kt`: the routes under `/v1/groups`, with the literal `invitations` registered before
    `:groupId`; `Context.getGroupIdFromPathParam()` (URL-safe base64 to `GroupId`, 404 otherwise)
    next to `getContactIdFromPathParam()`.
  - A post reads `getPreviousMsgId` and `getGroupCount`, signs and calls `addLocalMessage` in one write
    transaction; an invitation gets its timestamp and timer, signs and sends in one write
    transaction. Both refuse a dissolved group.
  - `README.md`: a section per endpoint and event, in the existing format.
  - No changes in `briar-core`, `bramble-*` or the app, and no new dependencies (so no witness
    changes). Estimate: about 450 lines of Kotlin, 800 lines of tests, 300 lines of README.
- *Tests in upstream style.* Unit tests per controller on `ControllerTest` with `mockk`, checking the
  JSON with JSONAssert (`STRICT`), plus bad input (missing, empty, too long, unknown ids) and output
  tests for events (`MessagingControllerImplTest`, `ForumControllerTest`). Integration tests on
  `IntegrationTest` start a real peer on port 8000 without transports and call it with OkHttp;
  `TestDataCreator` makes contacts, groups and incoming posts (`createPrivateGroups`,
  `createRandomPrivateGroupMessages`). `./gradlew --configure-on-demand briar-headless:test` in
  `eclipse-temurin:17-jdk` runs the 99 existing tests, all passing: 2 min with an empty Gradle cache,
  1 min with a warm one.
- *LAN transport: no.* `HeadlessModule.providePluginConfig` lists one duplex plugin, Tor for the OS,
  and no simplex ones; the app adds LAN and Bluetooth. Briar's LAN plugin (`LanTcpPluginFactory`) is
  in the platform-independent `bramble-core`, and `bramble-java` has a Bluetooth plugin
  (`JavaBluetoothPluginFactory`), so adding LAN is a few lines in `providePluginConfig`. The LAN
  plugin does no discovery: each peer shares its last few LAN addresses (IPv4 `ip:port`, IPv6) with
  its contacts as transport properties, through any transport, and dials the contacts' addresses (plus
  the Wi-Fi hotspot addresses) when it polls (`LanTcpPlugin`; Briar wiki "How Briar Connects to
  Contacts"). So a phone and a home hub on the same Wi-Fi without internet connect only if one of them
  knows the other's current address from an earlier sync; a fixed LAN address for the hub helps.
  In Docker the hub would need host networking: in a bridge network it would share the container's
  address, which phones cannot reach. Not needed for v1; a later patch for the home hub (D17, D29).
- *Upstream contribution rules* (wiki pages "development-workflow", "pre-review-checklist",
  "code-style", "development-101"; `.gitlab-ci.yml`). Code is on Briar's own GitLab,
  <https://code.briarproject.org/briar/briar>. Work starts from a ticket; the branch is named after it
  (`1664-…`); before a merge request all tests pass and the pre-review checklist is met (thread
  safety, minimal visibility, checked exceptions, no blocking in event handlers, no transactions
  while holding locks), the branch is rebased on `master`, and at least one maintainer reviews it.
  CI runs `animalSnifferMain animalSnifferTest`, `assembleOfficialDebug :briar-headless:linuxJars`
  and `check`. Style: Java with tabs and 80 columns, Kotlin in the official Kotlin style; static
  imports where they do not hurt clarity; classes package-private (in Kotlin `internal`) behind
  interfaces bound with Dagger. Dependencies are pinned with Gradle Witness. The code is GPL-3.0; the
  pages read ask for no contributor agreement.
- *Upstream state.* Issue [#1664](https://code.briarproject.org/briar/briar/-/issues/1664) "join forums
  or groups in headless api" (feature request, "Good first issue", "Headless") is open since 2019.
  Two community merge requests that add forum endpoints to headless,
  [!1826](https://code.briarproject.org/briar/briar/-/merge_requests/1826) (2024-06) and
  [!1831](https://code.briarproject.org/briar/briar/-/merge_requests/1831) (2024-09), are still open,
  and since mid-2023 `briar-headless` has only had dependency and Tor upgrades. A merge of our patch is
  therefore uncertain and probably slow, so we carry it in our fork (D29). Neither merge request can
  be reused: both cover forums (`ForumManager`, `ForumSharingManager`), a different Briar client
  with no creator, no dissolve and no signed invitations. !1826 answers half of its routes with 501,
  prints debug output, mixes hex and base64 ids, puts standard base64 ids in URL paths and has no
  tests. !1831 finds forums by name, answers errors with 200, sends no events despite its
  description, and only fixes the existing test's constructor.

## Phase 0 wrap-up (2026-10-01, session S07)

Every (verify) of design.md was walked through. What v1 relies on is answered by S1–S3; what is left
concerns only the Kyiv broker and gateways, which v1 does not use (D27, D30), and is listed in
design.md §6.6.

Read in the firmware source of the lab's version (`meshtastic/firmware` at `54e0d8d`, 2.7.26):
- `AdminModule.cpp`: when the region is first set, `ignore_mqtt` is turned on if the region's duty
  cycle is below 100 %. `RadioInterface.cpp` gives `EU_433` and `EU_868` 10 %, `UA_433` 10 % and
  `UA_868` 1 %. So radios set up for the Kyiv mesh (`EU_433`) start with "Ignore MQTT" on, like
  `EU_868`. The same code appends the region to the MQTT root only while it is still `msh`.
- `MQTT.cpp`, `MQTT.h`: nodes with `WiFiClientSecure` (ESP32 on Wi-Fi) support `tls_enabled` (port
  8883 by default) and call `setInsecure()`, so they do not check the broker's certificate; nodes
  without it refuse a TLS config. A gateway uploads other nodes' decodable packets only with their "OK
  to MQTT" bit, unless the broker's address is private (10/8, 172.16/12, 192.168/16, 100.64/10,
  169.254/16, 127.0.0.1); PKI packets it cannot decode are uploaded regardless.

Still open, and where it is answered:

| Item | Where |
|---|---|
| The Kyiv questions of design.md §6.6 (broker routing, PKI and downlink policy, gateways, "Ignore MQTT" on relays, "OK to MQTT", a bot node) | roadmap S04 (a claimed physical node, `lab/spike_kyiv.py`) and S23 |
| A physical node as the hub's node (serial, BLE, TCP) | later, after v1 (D20) |
| ARM64: `meshtasticd` was checked only in the manifest, our `briar-headless` jar was built but not run | S25 (the image) and S29 (the server) |
| Private groups switched on for an existing Briar account need no migration (read in the source) | answered in S24 with a phone: none needed (D51) |
| `RATE_LIMIT_EXCEEDED` addressed to node 0 (S2, part 2): a firmware bug worth reporting upstream | anyone, not blocking |

**Result, S23 part 1 (2026-10-05, the community broker `mqtt.wikimesh.in.ua`, D62; owner's login; read-only except the owner's own node):**
- *The broker.* Login accepted; topics are `kyiv/2/e/<channel>/!<gateway>`, `kyiv/2/c/…` and `kyiv/2/stat/!<gateway>`; every envelope seen
  in minutes was already decoded (`encrypted, not decoded: 0`), the owner's node having *Encryption Enabled* off as told.
  Traffic seen: position, telemetry, traceroute, routing; no text from other people within 4 minutes.
- *The owner's node as a gateway.* A nRF52840 (no Wi-Fi) reaches the broker through the Android app's MQTT client proxy
  (the node's "proxy to client" on and the phone's proxy switch on). A text sent on `LongFast` appeared on the broker as
  `kyiv/2/e/LongFast/!<node>`, three copies seven seconds apart; the app showed "Failed to deliver to mesh" (no radio
  neighbour repeated it), which does not mean MQTT failed. Firmware 2.8.1 had to turn *Ignore MQTT* off and *Ok to MQTT* on
  by hand (the first-time duty-cycle region pitfall).
- *The lab's `wikimesh` probe* (`lab/spike_wikimesh.py`, a `meshtasticd` 2.7.26 with the Kyiv channels, encryption off, uplink off):
  it connects, subscribes to `…/e/<channel>/+` of its channels and `…/e/PKI/+` (not `c/`), and **reads the broker: its router logs
  `Received text msg` for the owner's node's texts (sent on a private channel of our own, `chatkotest`, a random PSK on index 2 of both
  nodes; the community's nodes do not subscribe to its name, so it stays private, unlike `LongFast`) and for a real person's text
  (a decoded envelope from another gateway, accepted because the probe's own `encryption_enabled` is off).** The first runs looked like
  "not handled" for two reasons: the log shows nothing about a received packet at `info`, and the probe's `ignore_mqtt` was **on** (the
  first-time duty-cycle region pitfall; the router logged `Msg came in via MQTT` at `debug` and dropped the packet). The script
  now has to write the LoRa config a second time like `lab/provision.py`; until then, `meshtastic --set lora.ignore_mqtt false`.
  The 2.8.1 node's packets are handled by a 2.7.26 `meshtasticd`.
- *Both ways, on a private channel (2026-10-05).* A text sent by the probe on `chatkotest` reached the owner's node and the app
  once **both sides had MQTT encryption on**. With the probe's encryption off it published *decoded* envelopes, and the node's
  serial log (`meshtastic --noproto`) said `Ignore decoded msg on MQTT, encryption enabled`: stock firmware drops a decoded
  envelope when its own `encryption_enabled` is on (and accepts only decoded ones from others when it is off, which is why the
  probe could read the community's decoded packets). **One node cannot do both**: to talk privately it needs encryption on, to read
  the community's decoded traffic it needs it off. Privacy note: with encryption off even a private channel's text is plain on the
  broker for anyone listening to `kyiv/#`; with it on only the channel name, the sender id, the size and the time are visible.
  Downlink through the phone's proxy works; the node subscribes by itself after a channel change plus a reboot.
  **Not stable yet:** of three encrypted texts sent in a row (the probe's 4th, 5th and 6th) only the last one appeared in the app,
  although all three were on the broker and the node logged `Received MQTT topic …` for the one it was watching; the cause is open
  (the phone's BLE or proxy reconnecting after the node's reboot, duplicate packet ids after the probe restarted, or the node
  subscribing only after a serial client connected). To check: five texts 20 s apart, no serial client, a steady phone link,
  and count. Not yet checked either: PKI direct messages, and the hub in a container.
- *A pitfall.* `meshtasticd` prints the MQTT password in its log at start; do not paste logs.
