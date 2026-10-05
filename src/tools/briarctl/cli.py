"""The `briarctl` command line (design.md §7.5): arguments, settings, output and exit codes.

Exit codes: 0 done, 1 a command failed (nothing was done, or some of its items failed), 2 wrong
arguments or settings.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from briarctl import commands, ids
from briarctl.api import BriarClient, BriarError
from briarctl.http_client import HttpBriarClient

DEFAULT_URL = "http://127.0.0.1:7000"
URL_VARIABLE = "BRIARCTL_URL"
TOKEN_VARIABLE = "BRIAR_AUTH_TOKEN"  # noqa: S105 - the name of the variable, not a secret

Connect = Callable[[str, str], BriarClient]


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    confirm: commands.Confirm,
    connect: Connect,
) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return int(stop.code or 0) if isinstance(stop.code, int) else 2
    try:
        url, token = _settings(args, env)
    except ValueError as error:
        print(f"briarctl: {error}", file=stderr)
        return 2
    try:
        outcome = args.handler(commands.Context(connect(url, token), confirm), args)
    except BriarError as error:
        print(f"briarctl: {error}", file=stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(outcome.data, indent=2, ensure_ascii=False), file=stdout)
    else:
        print("\n".join(outcome.lines), file=stdout)
    return 1 if outcome.failed else 0


def main() -> None:
    sys.exit(
        run(
            sys.argv[1:],
            env=os.environ,
            stdout=sys.stdout,
            stderr=sys.stderr,
            confirm=_ask,
            connect=HttpBriarClient,
        )
    )


def _ask(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _settings(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[str, str]:
    url = getattr(args, "url", None) or env.get(URL_VARIABLE) or DEFAULT_URL
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"the API address must be an http(s) URL, not {url!r}")
    if parts.username or parts.password:
        raise ValueError("the API address must not hold a user name or a password")
    token_file = getattr(args, "token_file", None)
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError(f"cannot read the token file {token_file}: {error.strerror}") from None
    else:
        token = env.get(TOKEN_VARIABLE, "").strip()
    if not token:
        raise ValueError(f"no token: set {TOKEN_VARIABLE} or give --token-file")
    return url, token


def _group_id(text: str) -> str:
    try:
        return ids.normalize(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print JSON instead of text")  # fmt: skip
    common.add_argument("--url", default=argparse.SUPPRESS,
                        help=f"the API address (${URL_VARIABLE}; {DEFAULT_URL})")  # fmt: skip
    common.add_argument("--token-file", default=argparse.SUPPRESS, metavar="PATH",
                        help=f"a file with the API token (default: ${TOKEN_VARIABLE})")  # fmt: skip

    parser = argparse.ArgumentParser(
        prog="briarctl",
        description="Manage the hub's Briar account: contacts, groups and invitations.",
        parents=[common],
    )
    top = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def leaf(
        parent: "argparse._SubParsersAction[argparse.ArgumentParser]",
        name: str,
        handler: commands.Handler,
        help_text: str,
    ) -> argparse.ArgumentParser:
        sub = parent.add_parser(name, help=help_text, description=help_text, parents=[common])
        sub.set_defaults(handler=handler)
        return sub

    leaf(top, "link", commands.link, "print the hub's briar:// link")

    contact = top.add_parser("contact", help="the account's contacts").add_subparsers(
        dest="action", required=True, metavar="ACTION"
    )
    sub = leaf(contact, "add", commands.contact_add, "add a contact by its briar:// link")
    sub.add_argument("link")
    sub.add_argument("--alias", default="contact", help="the name to call them (default: contact)")
    leaf(contact, "list", commands.contact_list, "list contacts and the ones being added")
    sub = leaf(
        contact,
        "remove",
        commands.contact_remove,
        "remove a contact, or stop adding one that is still pending",
    )
    sub.add_argument("contact", help="id, alias or name (of a pending contact: alias)")
    sub.add_argument("--yes", action="store_true", help="do not ask")

    invitation = top.add_parser(
        "invitation", help="invitations to groups others made"
    ).add_subparsers(dest="action", required=True, metavar="ACTION")
    leaf(invitation, "list", commands.invitation_list, "list the invitations, with group ids")
    sub = leaf(invitation, "accept", commands.invitation_accept, "join the group")
    sub.add_argument("group", type=_group_id)
    sub = leaf(invitation, "decline", commands.invitation_decline, "refuse the invitation")
    sub.add_argument("group", type=_group_id)

    group = top.add_parser("group", help="the hub's private groups").add_subparsers(
        dest="action", required=True, metavar="ACTION"
    )
    leaf(group, "list", commands.group_list, "list the groups the hub is in")
    sub = leaf(group, "members", commands.group_members, "list a group's members")
    sub.add_argument("group", type=_group_id)
    sub = leaf(group, "reveal", commands.group_reveal,
               "reveal the hub's relationship with members who are its contacts")  # fmt: skip
    sub.add_argument("group", type=_group_id)
    sub.add_argument("contacts", nargs="+", metavar="contact", help="id, alias or name")
    sub = leaf(group, "create", commands.group_create, "create a group with the hub as its creator")
    sub.add_argument("name")
    sub = leaf(group, "invite", commands.group_invite, "invite contacts to a group the hub created")
    sub.add_argument("group", type=_group_id)
    sub.add_argument("contacts", nargs="+", metavar="contact", help="id, alias or name")
    sub.add_argument("--text", help="a note with the invitation")
    sub = leaf(group, "dissolve", commands.group_dissolve,
               "dissolve a group the hub created, or leave one someone else created")  # fmt: skip
    sub.add_argument("group", type=_group_id)
    sub.add_argument("--yes", action="store_true", help="do not ask")
    return parser
