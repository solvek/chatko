# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "websockets>=13"]
# ///
"""Spike S3a: the briar-headless contacts and private-message API (design.md §7.1, D23).

Talks to the lab briar-headless (lab/briar/) on 127.0.0.1:7000. The token is read from the
container unless BRIAR_TOKEN is set. Not chatko code.

    uv run lab/spike_briar.py link                    # the hub's briar:// link
    uv run lab/spike_briar.py add <link> [--alias A]  # add a pending contact
    uv run lab/spike_briar.py pending | contacts
    uv run lab/spike_briar.py watch [--seconds N]     # print WebSocket events with timestamps
    uv run lab/spike_briar.py send <contactId> <text> [--wait N]  # send, then watch for acks
    uv run lab/spike_briar.py messages <contactId>
"""

from __future__ import annotations

import argparse
import asyncio
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
WS_URL = "ws://127.0.0.1:7000/v1/ws"
COMPOSE_FILE = Path(__file__).parent / "briar" / "docker-compose.yml"


def token() -> str:
    if env := os.environ.get("BRIAR_TOKEN"):
        return env
    out = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "briar", "cat", "/data/auth_token"],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return out.stdout.strip()


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
    async with websockets.connect(WS_URL) as ws:
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
    ap = argparse.ArgumentParser()
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
    a = ap.parse_args()
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


if __name__ == "__main__":
    main()
