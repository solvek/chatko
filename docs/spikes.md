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
- [ ] A PKI direct message radio → hub and hub → radio. Does the Python API expose `pki_encrypted`
  and the sender's `public_key` on received packets?
- [ ] How the two nodes learn each other's public keys over MQTT (NodeInfo on the primary channel),
  and how long it takes after a restart. Can the hub request NodeInfo from a member's node?
- [ ] ACKs for direct messages sent through MQTT: does the hub get them?
- [ ] De-duplication fields: are `(from, id)` stable across gateways?
- [ ] The node identity (node id, keys) persists across container restarts (volume).
- [x] Setting channels, PSKs, names and MQTT settings from code or config files, so the hub can
  provision its node.
- [ ] Read-only connection to the Kyiv broker: `LongFast` text messages are received. Broker policy
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

Left for part 2 (S03): PKI direct messages, key learning, ACKs, `(from, id)` across gateways, node
identity across restarts. Left for S04: the Kyiv broker. The `ignore_mqtt` default on Kyiv radios and
relays is a question for the Kyiv community (S04).

## S3. briar-headless build and API

**Question:** can we build and run `briar-headless` locally, and how big is the private-group patch?

Steps and answers needed:
- [ ] Build `x86LinuxJar` (and `aarch64LinuxJar`) from the current upstream; the JDK version needed.
- [ ] Run it in Docker with a persistent data volume and a non-interactive account creation.
- [ ] Contacts API end to end with a phone: exchange links, `ContactAddedEvent`, private messages both
  ways over the WebSocket. Confirm that a contact at a distance needs **both** sides to add the other's
  link, and write down the exact `curl` calls the admin will use (D23).
- [ ] Read `PrivateGroupManager`, `GroupInvitationManager` and how the Android app uses them. List the
  methods for the patch (§7.4 of the design): create, list, members, invite, dissolve, invitations from
  others, read and post (D24).
- [ ] Does `briar-headless` include Briar's LAN (Wi-Fi) transport, so a hub without internet can sync
  with phones on the same network? Not needed for v1; it decides the future home hub (D17).
- [ ] Upstream contribution rules for briar-headless (code style, tests, merge request process).

**Result:** _not run yet._
