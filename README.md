# chatko

**chatko keeps a closed group of people talking by text when some of them have no mobile network or
internet.** A group lives in several networks at once: a Telegram group, a Briar private group, and
Meshtastic LoRa channels. A hub copies every message to all the other places.

```
 Telegram group ◄──► chatko hub ◄──► Meshtastic channel (virtual node → MQTT → gateway node → radios)
                         ▲
                         └──► Briar private group (members also relay it to each other over Bluetooth/Wi-Fi)
```

- One installation serves several independent groups.
- Every network is an **extension**: Telegram, Meshtastic and Briar today, and more can be added later.
  Telegram also provides sign-in and account management for now, and that part can be replaced too.
- It can forward the public Meshtastic chat (e.g. the Kyiv `LongFast`) to selected members.

> **Status: design.** No code yet. Start with the [design](docs/design.md).

## Known limitations

- **A cloud hub reaches radios only through an internet-connected gateway node.** A radio node without
  internet only relays. chatko sends to radios as direct messages to each member's node (works through
  any gateway that forwards them, one packet per node), or as one broadcast on a private group channel
  (needs a gateway that knows the channel, in practice your own). A hub within radio range can use a
  physical node instead and needs no gateway.
- **Radios must not ignore MQTT, and must allow it.** Messages from a cloud hub reach the air through
  MQTT, and a radio with "Ignore MQTT" on drops them (and does not relay them). Meshtastic turns this
  setting on by default when the region is set to `EU_868`, so members turn it off on their radios.
  Members also turn "OK to MQTT" on (it is off by default): without it, gateways on a public broker do
  not pass their messages and acknowledgements on to the hub.
- **Direct messages to radios need keys on both sides.** Meshtastic encrypts a direct message with the
  receiver's key, and a node keeps the first key it learns for another node. When a member resets
  their radio, the admin puts its new key into the config. Keep the hub's own key in the config
  (`private_key`), so that it never changes, even if the hub's data is lost.
- **Removing a person from the mirrors is not instant.** Membership follows the Telegram group. To remove
  someone from a Meshtastic channel, set a new channel key on all radios. Briar cannot remove a member
  from a private group, and deleting the contact does not cut them off (they still sync through other
  members). The only remedy is to re-create the Briar group.
- **Briar reaches offline members only through other members.** A member who is never online gets Briar
  messages only if another group member (a Briar contact of theirs) syncs with the hub and later meets
  them, and one of the two has used "Reveal contacts" in the group. Direct Briar messages are never
  relayed like this, which is one reason chatko has group messages only.
- **The hub is a single point of failure.** Keep backups of `config/` and `data/`.

## Documentation

- [Design](docs/design.md): behaviour, concepts, extensions, routing, configuration, plan
- [Architecture](docs/architecture.md): code structure, extension API, quality gates
- [Decisions](docs/decisions.md): key decisions and why
- [Spikes](docs/spikes.md): experiments that answer the open questions
- [Roadmap](docs/roadmap.md): the plan by working session
- [`config.example.yaml`](config.example.yaml), [`.env.example`](.env.example): configuration format
- [AGENTS.md](AGENTS.md): guide for contributors and AI coding agents

## Built on

- [meshtasticd](https://meshtastic.org/docs/software/linux/): the official Meshtastic Linux node, used as the virtual node
- [briar-headless](https://code.briarproject.org/briar/briar/-/tree/master/briar-headless): the Briar REST API peer
- Telegram Bot API

## License

[GPL-3.0-or-later](LICENSE).
