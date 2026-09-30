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
  ways over the WebSocket. Confirm that a contact at a distance needs **both** sides to add the other's
  link, and write down the exact `curl` calls the admin will use (D23).
- [ ] Read `PrivateGroupManager`, `GroupInvitationManager` and how the Android app uses them. List the
  methods for the patch (§7.4 of the design): create, list, members, invite, dissolve, invitations from
  others, read and post (D24).
- [ ] Does `briar-headless` include Briar's LAN (Wi-Fi) transport, so a hub without internet can sync
  with phones on the same network? Not needed for v1; it decides the future home hub (D17).
- [ ] Upstream contribution rules for briar-headless (code style, tests, merge request process).

**Result:** _not run yet._
