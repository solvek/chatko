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
- [ ] Relay works H → B → A and A → B → H.
- [ ] One-sided reveal is enough / both sides must reveal.
- [ ] A member can / cannot be removed; the effect of deleting the contact.
- [ ] Background behaviour and time until delivery.

**Result:** _not run yet._

## S2. Local Meshtastic lab (no hardware)

**Question:** does `meshtasticd` with a simulated radio work as a virtual node over MQTT, and what does
the Python API expose?

Setup: `docker compose` with Mosquitto and two `meshtasticd` containers without a radio. **hub**
plays chatko's virtual node; **radio** plays a member's node. Both connect to the local broker with
uplink and downlink enabled on the private channel.

Steps and answers needed:
- [ ] The Docker image and the config needed for a simulated radio; the `linux/amd64` and
  `linux/arm64` images exist.
- [ ] A private-channel message radio → hub arrives through MQTT and is visible via `TCPInterface`
  (portnum, from, channel index, packet id); hub → radio in the opposite direction.
- [ ] A PKI direct message radio → hub and hub → radio. Does the Python API expose `pki_encrypted`
  and the sender's `public_key` on received packets?
- [ ] How the two nodes learn each other's public keys over MQTT (NodeInfo on the primary channel),
  and how long it takes after a restart. Can the hub request NodeInfo from a member's node?
- [ ] ACKs for direct messages sent through MQTT: does the hub get them?
- [ ] De-duplication fields: are `(from, id)` stable across gateways?
- [ ] The node identity (node id, keys) persists across container restarts (volume).
- [ ] Setting channels, PSKs, names and MQTT settings from code or config files, so the hub can
  provision its node.
- [ ] Read-only connection to the Kyiv broker: `LongFast` text messages are received. Broker policy
  for PKI direct messages (topic `…/2/e/PKI/…`) and for downlink (ask the Kyiv community). Do the Kyiv
  gateways downlink PKI direct messages to nodes they hear? This decides whether `dm` mode works
  without our own gateway.
- [ ] Later, with hardware: the same tests with a physical node over USB/TCP/BLE as the hub's node.

**Result:** _not run yet._

## S3. briar-headless build and API

**Question:** can we build and run `briar-headless` locally, and how big is the private-group patch?

Steps and answers needed:
- [ ] Build `x86LinuxJar` (and `aarch64LinuxJar`) from the current upstream; the JDK version needed.
- [ ] Run it in Docker with a persistent data volume and a non-interactive account creation.
- [ ] Contacts API end to end with a phone: exchange links, `ContactAddedEvent`, private messages both
  ways over the WebSocket.
- [ ] Read `PrivateGroupManager`, `GroupInvitationManager` and how the Android app uses them. List the
  methods for the patch (§7.4 of the design), and check whether removing a member is supported.
- [ ] Upstream contribution rules for briar-headless (code style, tests, merge request process).

**Result:** _not run yet._
