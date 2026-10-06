# Meshtastic testing

The current state of what has been tried with real radios and brokers, the firmware rules that decide
whether a message gets through, what is still open, and the test plan. Unlike `spikes.md` and
`decisions.md`, this document is **kept current**: rows change when a test answers them. The history
(how each result was found) stays in `spikes.md` (S2, S23) and the decisions in `decisions.md` (D58,
D59, D62, D63).

## 1. The goal

Two scenarios for a member (here the owner) whose radios have **no internet**, only LoRa to other nodes
of the mesh, some of which are gateways (a node with MQTT on) somewhere in the city:

- **A, radio → hub.** The member's node sends a direct message to the hub's node (the `chatko` contact);
  it reaches the hub and is relayed to Telegram and Briar.
- **B, hub → radio.** A message in Telegram or Briar goes from the hub to the broker, a gateway puts
  it on the air, and it reaches the member's node over LoRa, possibly over relays.

Both need a gateway that the member does not run: someone else's node. The question is what that
gateway must have, and which of it we can choose.

## 2. The setup now (production, 2026-10-06)

| Part | State |
|---|---|
| Broker | `mqtt.wikimesh.in.ua:1883`, root `kyiv`, one shared login (D62, D63). It is **not** the broker of `meshtastic.kyiv.ua` (`mqtt.meshtastic.kyiv.ua`): a separate community, other gateways |
| Hub's node | virtual (`meshtasticd` 2.7.26), long name `chatko`, MQTT encryption **on**, one channel: `chatko` (private PSK) at index 0 with uplink and downlink |
| Owner's node | `!050c0a66`, nRF52840 on 2.7.26; channels `chatko` (0), the Kyiv `LongFast` (1), `KyivUA` (2); MQTT encryption on, gateway through the phone app's MQTT proxy; `ignore_mqtt` off, OK to MQTT on. `deploy/configure-node.sh` sets a node this way |
| Group | `crisis` has `dm: ["!050c0a66"]` (D59: direct messages only) |
| Second node | not yet |

## 3. What has been tried

| What | Result | Where |
|---|---|---|
| A DM hub → owner's node, the node being its own gateway (phone proxy) | works, ACKed at the first attempt once both have the same channel 0 | spikes.md S23, D63 |
| A DM owner's node → hub, same path | works (the probe in S23: 3 of 3; production: the owner's replies reach Telegram) | spikes.md S23 |
| Channel broadcasts through the same path | lossy, about a third lost, silently | spikes.md S23 |
| Different channel 0 on the two ends | the ACK is lost both ways: 9 retries, the text shown again and again, "Failed to deliver" | spikes.md S23 |
| A node with MQTT encryption on and a decoded envelope | dropped (`Ignore decoded msg on MQTT, encryption enabled`); so the owner's node lists none of the community's nodes | spikes.md S23 |
| Anything through **someone else's** gateway | **not tried** | — |
| A relay between the gateway and the member | **not tried** (the owner's node has had no radio neighbour so far) | — |

## 4. The firmware rules (2.7.26 source)

Read in `meshtastic/firmware` at `v2.7.26.54e0d8d`. These decide the scenarios more than anything we
configure.

1. **A text DM is always PKI.** A node sends a direct text only PKI-encrypted with the receiver's public
   key; without the key it refuses ("refusing to send legacy DM"), it does not fall back to the channel
   key. The packet's channel byte is 0. (`src/mesh/Router.cpp` 642–676.)
2. **A gateway uplinks someone else's PKI DM only with MQTT encryption on.** A DM it cannot decrypt is
   marked for MQTT only when `mqtt.encryption_enabled` is on, and `onSend` drops a still-encrypted
   packet when it is off ("Don't upload a still-encrypted PKI packet if not encryption_enabled").
   Uplink must also be on for at least one channel; the DM's channel does not matter.
   (`Router.cpp` 795–801, `src/mqtt/MQTT.cpp` 768–812.)
3. **A gateway downlinks a PKI DM only if it has downlink on some channel and knows both nodes.** It
   subscribes to `<root>/2/e/PKI/+` only when a channel has downlink on, and puts a PKI packet on the
   air only if the packet is to itself or **both the sender and the receiver are in its node database
   with their user info** (heard NodeInfo). Its own encryption setting does not matter for an encrypted
   envelope. (`MQTT.cpp` 71–89, 141–150, 581–605.)
4. **A node with MQTT encryption on drops every decoded envelope**, and one with it off publishes
   everything it decoded in plain text. (`MQTT.cpp` 129–133, 804–812.)
5. **The ACK of a DM travels on the receiver's channel index 0**, with that channel's key: a PKI DM
   arrives with channel 0, and the ACK goes back on that index, not PKI (routing packets are never
   PKI). So the two ends need the same channel 0 (name and key), and a gateway carries the ACK only if
   it has that channel too: uplink needs it to decode the packet, downlink needs it subscribed with
   downlink on. (`ReliableRouter.cpp` 92–130, `Router.cpp` 652–655.)
6. **NodeInfo is a channel packet** (never PKI), sent on the primary channel. A gateway learns a node
   only from a channel it has: the member's node over LoRa, the hub's node from MQTT downlink.
7. **OK to MQTT.** On a broker that is not a private address, a gateway uplinks someone else's
   *decoded* packet (NodeInfo, ACKs, channel texts) only if the sender set "OK to MQTT"; PKI DMs it
   could not decode are not checked. (`MQTT.cpp` 779–787.)
8. **Ignore MQTT** on any relay between the gateway and the member drops the hub's packets (design.md
   §6.2).
9. **A gateway's own packet coming back from the broker is an implicit ACK** for its sender only, not
   a delivery (`MQTT.cpp` 93–102); the extension already counts only the destination's ACK.

## 5. What follows for the two scenarios

| Gateway (someone else's node) | A, radio → hub | B, hub → radio | ACKs |
|---|---|---|---|
| MQTT encryption **off** (what the wikimesh community's gateways do: every envelope seen in S23 was decoded) | **no**: rule 2, the DM is never uplinked, whatever the hub does | only if it knows the hub's node (rule 3), so only with a shared channel 0 with downlink | the member's ACK is uplinked decoded and the hub (encryption on) drops it (rule 4) |
| MQTT encryption **on**, without our channel 0 | yes (rule 2) | no: it does not know the hub's node (rules 3, 6) | lost both ways (rule 5) |
| MQTT encryption **on**, **with our channel 0** and downlink on it | yes | yes | yes |

So:
- **The hub's own settings cannot make scenario A work through a gateway with encryption off.** Turning
  encryption off on the hub does not help A (rule 2 is the gateway's setting), and it would publish
  the hub's DMs in plain text (rule 4).
- **Channel 0 must be shared by the hub, the member's node and the gateway.** With a private `chatko` a
  gateway needs its key, so it must be a gateway we set up. With the Kyiv `LongFast` (public key) as
  channel 0 on the hub and the member's node, any community gateway with `LongFast` downlink and
  encryption on would do, at the cost of the hub being visible on the public channel (D58, design.md
  §6.6). T1 found no community gateway with encryption on, and the community's `LongFast` probably
  uses the default key, not the Kyiv one (§7).
- In practice the reliable path is **a gateway we configure**: a friend's node, or a node of ours at a
  place with internet, with encryption on, uplink and downlink on our channel 0, the hub's contact
  imported (so it knows the hub at once), and `ignore_mqtt` off.

## 6. Test plan

Production is used for the tests (the owner's decision, 2026-10-06: only we are there). Each test
notes the hub's log (`dc logs chatko`), the broker (`lab/survey_broker.py`, T1) and both apps.

| # | Setup | Expect | Status |
|---|---|---|---|
| T1 | Passive: subscribe to `kyiv/#` with the hub's login, publish nothing | which gateways and channels exist, how many publish encrypted (encryption on) vs decoded | see §7 |
| T2 | Two own nodes: **G** (the gateway: MQTT through the phone proxy) and **M** (the member: no MQTT, phone only over Bluetooth), in radio range of each other; channel 0 `chatko` on hub, G and M; G encryption on | A and B work and are ACKed both ways: the baseline of a gateway we configure | todo (needs the second node) |
| T3 | As T2, G encryption **off** | A fails silently (rule 2); B arrives, M's ACK is dropped by the hub, retries | todo |
| T4 | As T2, G without `chatko` (only `LongFast`, `KyivUA` with downlink), encryption on | A arrives, M sees "Failed to deliver"; B is not put on the air (G does not know the hub) | todo |
| T5 | Channel 0 the Kyiv `LongFast` on hub, G and M (no `chatko`), G encryption on | A, B and the ACKs work: the model of "any community gateway with encryption on" | todo |
| T6 | As T5 with G and the hub encryption **off** | the no-encryption fallback: A fails (rule 2); whether B arrives as a non-PKI packet and what the broker shows in plain text | todo, only if T5 is not enough |
| T7 | M out of G's range, a relay between them | `ignore_mqtt` on relays, hop counts | todo (field) |
| T8 | M somewhere with only community gateways around, the owner's G off | the real scenario | todo (field) |

A part of T2–T5 can be tried before the second node arrives: the owner's node as G and the lab's
`wikimesh` probe or the hub itself as the far end, with the phone proxy switched off between tries.
That checks the gateway's MQTT rules but not the radio hop.

## 7. Results

**T1 (2026-10-06, about 18 minutes in three runs, production's login, publish nothing;
`lab/survey_broker.py`).** The broker is quiet: 15 distinct packets in 10 minutes (NodeInfo, telemetry,
store-and-forward, traceroute, position; one text, the owner's own).
- **No gateway publishes encrypted envelopes, apart from the owner's node.** About 14 gateways were
  seen (`!73a654bf`, `!f266e1e8`, `!5b0ad10f`, `!3834e331`, `!43b6c214`, `!017bd4c3`, `!2a42fb44`,
  `!eb9323c7`, `!756bd7f0`, `!e8e7a779`, …) and every envelope from them was decoded: their MQTT
  encryption is off. Not one packet appeared under `kyiv/2/e/PKI/`. By rule 2, none of these gateways
  would uplink a member's direct message to the hub.
- **The broker has a bridge across presets.** Each decoded packet a gateway uplinks on `LongFast`
  comes back under `kyiv/2/c/<preset>/!ffffffNN` and `kyiv/2/e/<preset>/!ffffffNN` for `LongFast`,
  `MediumFast`, `ShortFast` and `ShortTurbo` (fake gateway ids `!ffffff01`/`02`, `!fffffa01`/`02`,
  `!fffffb…`, `!fffffc…`; the envelope keeps the real gateway id), so gateways on other presets
  downlink it. The owner's encrypted packets (on `LongFast` with the Kyiv key, and on `chatko`) were
  not bridged: the bridge works on decoded packets only.
- **The community's channels look like the stock ones.** `MediumFast`, `ShortFast`, `ShortTurbo` are
  the preset names that stock firmware uses with the default key, so the community's `LongFast` is
  probably the default key too, not the Kyiv one. Unconfirmed: a decoded envelope does not show the key.
- `kyiv/2/stat/!<id>` holds retained `online`/`offline` for each gateway.

**What T1 changes.** Through the wikimesh community's gateways as they are, **scenario A cannot work
with direct messages**, and nothing on the hub's side changes that (rule 2). Scenario B can work only
through a gateway that knows the hub's node, which needs a channel the gateway shares with the hub
(rules 3, 6), and its ACKs need the hub to read decoded envelopes (rule 4). The options are in §8.

## 8. Options for a member without internet

1. **A gateway we configure (recommended).** A node with internet at a friend's place or ours, set by
   `deploy/configure-node.sh` with `MQTT_ADDRESS` (encryption on, our channel 0 with uplink and
   downlink). Everything works and stays private; the member's node needs radio range to it, directly or
   over relays with `ignore_mqtt` off. Tests T2, T7.
2. **Ask gateway owners to turn MQTT encryption on.** It would make their gateways carry PKI direct
   messages, but their decoded traffic would then leave the community's bridge (it works on decoded
   packets), so it is not realistic for the community's gateways.
3. **The no-encryption fallback: the community's public channel.** The member writes on the public
   `LongFast` (maybe with a tag); any community gateway uplinks it decoded and the bridge spreads it.
   The hub would read it with a **second virtual node** (a second Meshtastic extension instance) with
   MQTT encryption off and the community's channel as a `channel` site or a feed, and could answer the
   same way. It is a public broadcast: everyone on the mesh reads it, no ACK, lossy (S23: about a
   third of broadcasts lost), and the hub shows itself on the community's channel, which the community
   should agree to first (design.md §6.6). The direct messages stay on the first node. Untested; it
   needs the community's key of `LongFast` (above) and the owner's decision.
