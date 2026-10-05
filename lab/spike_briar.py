# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "websockets>=13"]
# ///
"""The lab client of briar-headless: contacts and private messages (spike S3a, design.md §7.1,
D23) and the private-group API of our fork (S24, design.md §7.4, D29).

Talks to the lab briar-headless (lab/briar/) on 127.0.0.1:7000, or the API at --url. The token is
read from the container unless BRIAR_TOKEN is set. A <group> is a group id in standard or URL-safe
base64. Not chatko code: `briarctl` does the same for the admin; this stays for the spikes.

    uv run lab/spike_briar.py link                    # the hub's briar:// link
    uv run lab/spike_briar.py add <link> [--alias A]  # add a pending contact
    uv run lab/spike_briar.py pending | contacts
    uv run lab/spike_briar.py watch [--seconds N]     # print WebSocket events with timestamps
    uv run lab/spike_briar.py send <contactId> <text> [--wait N]  # send, then watch for acks
    uv run lab/spike_briar.py messages <contactId>
    uv run lab/spike_briar.py groups | invitations    # our groups, invitations to others' groups
    uv run lab/spike_briar.py group-create <name> | group-dissolve <group>
    uv run lab/spike_briar.py members <group> | sharing <group>  # who joined, who can be invited
    uv run lab/spike_briar.py invite <group> <contactId> [--text T] | reveal <group> <contactId>
    uv run lab/spike_briar.py accept <group> | decline <group>
    uv run lab/spike_briar.py posts <group> | post <group> <text> | read <group> <messageId>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

BASE = "http://127.0.0.1:7000/v1"
COMPOSE_FILE = Path(__file__).parent / "briar" / "docker-compose.yml"


def token() -> str:
    if env := os.environ.get("BRIAR_TOKEN"):
        return env
    out = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "briar", "cat", "/data/auth_token"],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return out.stdout.strip()


def path_id(group: str) -> str:
    """A group id in standard or URL-safe base64, as URL-safe base64 for a path (design.md §7.4)."""
    padded = group.replace("+", "-").replace("/", "_") + "=" * (-len(group) % 4)
    raw = base64.urlsafe_b64decode(padded)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def show(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def call(client: httpx.Client, method: str, path: str, body: Any = None) -> Any:
    r = client.request(method, BASE + path, json=body)
    print(f"{method} {path} -> {r.status_code}", file=sys.stderr)
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return r.text


async def watch(tok: str, seconds: float) -> None:
    """Print every WebSocket event until `seconds` pass (0: forever)."""
    async with websockets.connect(BASE.replace("http", "ws", 1) + "/ws") as ws:
        await ws.send(tok)
        print(f"{stamp()} websocket open", file=sys.stderr)
        deadline = time.monotonic() + seconds if seconds else None
        while True:
            timeout = None if deadline is None else deadline - time.monotonic()
            if timeout is not None and timeout <= 0:
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout)
            except TimeoutError:
                return
            event = json.loads(raw)
            print(f"{stamp()} {event.get('name')}")
            show(event.get("data"))


async def send_and_watch(tok: str, client: httpx.Client, contact: int, text: str, wait: float) -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(watch(tok, wait))
        await asyncio.sleep(0.5)  # let the socket authenticate first
        print(f"{stamp()} sending")
        show(call(client, "POST", f"/messages/{contact}", {"text": text}))


def main() -> None:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7000", help="the API, without /v1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("link")
    p = sub.add_parser("add")
    p.add_argument("link")
    p.add_argument("--alias", default="phone")
    sub.add_parser("pending")
    sub.add_parser("contacts")
    p = sub.add_parser("watch")
    p.add_argument("--seconds", type=float, default=0)
    p = sub.add_parser("send")
    p.add_argument("contact", type=int)
    p.add_argument("text")
    p.add_argument("--wait", type=float, default=60)
    p = sub.add_parser("messages")
    p.add_argument("contact", type=int)
    sub.add_parser("groups")
    sub.add_parser("invitations")
    p = sub.add_parser("group-create")
    p.add_argument("name")
    for name in ("group-dissolve", "members", "sharing", "accept", "decline", "posts"):
        sub.add_parser(name).add_argument("group")
    p = sub.add_parser("invite")
    p.add_argument("group")
    p.add_argument("contact", type=int)
    p.add_argument("--text")
    p = sub.add_parser("reveal")
    p.add_argument("group")
    p.add_argument("contact", type=int)
    p = sub.add_parser("post")
    p.add_argument("group")
    p.add_argument("text")
    p = sub.add_parser("read")
    p.add_argument("group")
    p.add_argument("message")
    a = ap.parse_args()
    BASE = a.url.rstrip("/") + "/v1"
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    tok = token()
    with httpx.Client(headers={"Authorization": f"Bearer {tok}"}, timeout=30) as client:
        match a.cmd:
            case "link":
                show(call(client, "GET", "/contacts/add/link"))
            case "add":
                show(call(client, "POST", "/contacts/add/pending", {"link": a.link, "alias": a.alias}))
            case "pending":
                show(call(client, "GET", "/contacts/add/pending"))
            case "contacts":
                show(call(client, "GET", "/contacts"))
            case "watch":
                asyncio.run(watch(tok, a.seconds))
            case "send":
                asyncio.run(send_and_watch(tok, client, a.contact, a.text, a.wait))
            case "messages":
                show(call(client, "GET", f"/messages/{a.contact}"))
            case "groups":
                show(call(client, "GET", "/groups"))
            case "invitations":
                show(call(client, "GET", "/groups/invitations"))
            case "group-create":
                show(call(client, "POST", "/groups", {"name": a.name}))
            case "group-dissolve":
                show(call(client, "DELETE", f"/groups/{path_id(a.group)}"))
            case "members" | "sharing" | "posts":
                part = {"members": "members", "sharing": "invitations", "posts": "messages"}[a.cmd]
                show(call(client, "GET", f"/groups/{path_id(a.group)}/{part}"))
            case "invite":
                body = {"contactId": a.contact} | ({"text": a.text} if a.text else {})
                show(call(client, "POST", f"/groups/{path_id(a.group)}/invitations", body))
            case "reveal":
                body = {"contactId": a.contact}
                show(call(client, "POST", f"/groups/{path_id(a.group)}/members/reveal", body))
            case "accept" | "decline":
                body = {"accept": a.cmd == "accept"}
                show(call(client, "POST", f"/groups/invitations/{path_id(a.group)}", body))
            case "post":
                show(call(client, "POST", f"/groups/{path_id(a.group)}/messages", {"text": a.text}))
            case "read":
                body = {"messageId": a.message}
                show(call(client, "POST", f"/groups/{path_id(a.group)}/messages/read", body))


if __name__ == "__main__":
    main()
