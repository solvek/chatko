"""The commands of `briarctl` (design.md §7.5): each calls the client and returns an `Outcome`.

An `Outcome` holds what `--json` prints, the lines of the plain output and whether the command
failed for some of its items (a reveal or an invitation for several contacts goes on after one
fails). A call that fails altogether raises a `BriarError`.
"""

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from briarctl.api import (
    BriarClient,
    BriarError,
    Contact,
    Group,
    NotFoundError,
    PendingContact,
)

Confirm = Callable[[str], bool]
Handler = Callable[["Context", argparse.Namespace], "Outcome"]


class UsageError(BriarError):
    """The admin named something that is not there, or ambiguously."""


@dataclass(frozen=True)
class Context:
    client: BriarClient
    confirm: Confirm


@dataclass
class Outcome:
    data: Any
    lines: list[str] = field(default_factory=list)
    failed: bool = False


def link(ctx: Context, _args: argparse.Namespace) -> Outcome:
    address = ctx.client.link()
    return Outcome({"link": address}, [address])


def contact_add(ctx: Context, args: argparse.Namespace) -> Outcome:
    ctx.client.add_contact(args.link, args.alias)
    return Outcome(
        {"added": args.alias},
        [
            f"Adding {args.alias}. They add the hub's link (`briarctl link`) in Briar too; once "
            "both sides have, the contact appears in seconds (`briarctl contact list`)."
        ],
    )


def contact_list(ctx: Context, _args: argparse.Namespace) -> Outcome:
    contacts = ctx.client.contacts()
    pending = ctx.client.pending_contacts()
    lines = [_contact_line(contact) for contact in contacts]
    lines += [f"pending  {item.alias}  {item.state}" for item in pending]
    return Outcome(
        {"contacts": [asdict(c) for c in contacts], "pending": [asdict(p) for p in pending]},
        lines or ["No contacts."],
    )


def contact_remove(ctx: Context, args: argparse.Namespace) -> Outcome:
    contacts = ctx.client.contacts()
    waiting = [p for p in ctx.client.pending_contacts() if args.contact in (p.alias, p.id)]
    if waiting and not _matches(contacts, args.contact):
        return _cancel(ctx, args, waiting)
    contact = _find(contacts, args.contact)
    if not (args.yes or ctx.confirm(f"Remove the contact {contact.shown} (id {contact.id})?")):
        raise UsageError("not removed: answer yes, or give --yes")
    ctx.client.remove_contact(contact.id)
    return Outcome({"removed": contact.id}, [f"Removed {contact.shown} (id {contact.id})."])


def _cancel(ctx: Context, args: argparse.Namespace, waiting: list[PendingContact]) -> Outcome:
    """Stop adding a contact that has not completed: the API's only way to drop a failed one."""
    if len(waiting) > 1:
        raise UsageError(f"{args.contact!r} names several contacts that are being added")
    [pending] = waiting
    if not (args.yes or ctx.confirm(f"Stop adding {pending.alias} ({pending.state})?")):
        raise UsageError("not cancelled: answer yes, or give --yes")
    ctx.client.cancel_pending(pending.id)
    return Outcome({"cancelled": pending.id}, [f"Stopped adding {pending.alias}."])


def invitation_list(ctx: Context, _args: argparse.Namespace) -> Outcome:
    invitations = ctx.client.invitations()
    lines = [
        f"{i.group_id}  {i.name}  from {i.creator} (contact {i.contact_id})" for i in invitations
    ]
    return Outcome([asdict(i) for i in invitations], lines or ["No invitations."])


def invitation_accept(ctx: Context, args: argparse.Namespace) -> Outcome:
    return _answer(ctx, args.group, accept=True)


def invitation_decline(ctx: Context, args: argparse.Namespace) -> Outcome:
    return _answer(ctx, args.group, accept=False)


def _answer(ctx: Context, group_id: str, *, accept: bool) -> Outcome:
    invitation = next((i for i in ctx.client.invitations() if i.group_id == group_id), None)
    if invitation is None:
        raise NotFoundError("no invitation to that group (`briarctl invitation list`)")
    ctx.client.answer_invitation(group_id, accept=accept)
    if not accept:
        return Outcome({"declined": group_id}, [f"Declined {invitation.name}."])
    return Outcome(
        {"joined": group_id},
        [
            f"Joined {invitation.name}. Put this id into chatko.yaml: {group_id}",
            "Then reveal the hub's contacts there: `briarctl group reveal <group> <contact>…`.",
        ],
    )


def group_list(ctx: Context, _args: argparse.Namespace) -> Outcome:
    groups = ctx.client.groups()
    return Outcome([asdict(g) for g in groups], [_group_line(g) for g in groups] or ["No groups."])


def group_members(ctx: Context, args: argparse.Namespace) -> Outcome:
    group = _group(ctx, args.group)
    members = ctx.client.members(group.id)
    lines = []
    for member in members:
        role = "creator" if member.creator else "member"
        who = f"contact {member.contact_id}" if member.contact_id is not None else "not a contact"
        lines.append(f"{member.name}  {role}  {who}  {member.visibility}")
    invited: list[dict[str, Any]] = []
    if group.ours:
        names = {c.id: c.shown for c in ctx.client.contacts()}
        for sharing in ctx.client.sharing(group.id):
            if sharing.status == "invite_sent":
                name = names.get(sharing.contact_id)
                invited.append({"contact_id": sharing.contact_id, "name": name})
                lines.append(f"{name}  invited, not joined  contact {sharing.contact_id}")
    return Outcome({"members": [asdict(m) for m in members], "invited": invited}, lines)


def group_reveal(ctx: Context, args: argparse.Namespace) -> Outcome:
    group = _group(ctx, args.group)
    return _each_contact(ctx, args.contacts, lambda c: _revealed(ctx, group, c), key="revealed")


def _revealed(ctx: Context, group: Group, contact: Contact) -> str:
    ctx.client.reveal(group.id, contact.id)
    return f"Revealed the hub's relationship with {contact.shown} in {group.name}."


def group_create(ctx: Context, args: argparse.Namespace) -> Outcome:
    group = ctx.client.create_group(args.name)
    return Outcome(
        asdict(group),
        [
            f"Created {group.name}. Put this id into chatko.yaml: {group.id}",
            "Invite people with `briarctl group invite <group> <contact>…`.",
        ],
    )


def group_invite(ctx: Context, args: argparse.Namespace) -> Outcome:
    group = _group(ctx, args.group)

    def invite(contact: Contact) -> str:
        sharing = ctx.client.invite(group.id, contact.id, args.text)
        return f"Invited {contact.shown} to {group.name} ({sharing.status})."

    return _each_contact(ctx, args.contacts, invite, key="invited")


def group_dissolve(ctx: Context, args: argparse.Namespace) -> Outcome:
    group = _group(ctx, args.group)
    if group.ours:
        done, question = "Dissolved", f"Dissolve {group.name}? Its history is removed from the hub."
    else:
        done, question = "Left", f"Leave {group.name}? Its history is removed from the hub."
    if not (args.yes or ctx.confirm(question)):
        raise UsageError("not done: answer yes, or give --yes")
    ctx.client.leave_group(group.id)
    return Outcome({"left": group.id, "dissolved": group.ours}, [f"{done} {group.name}."])


def _each_contact(
    ctx: Context, references: list[str], action: Callable[[Contact], str], *, key: str
) -> Outcome:
    """Run `action` for each contact: one that fails is reported and the others still go on."""
    contacts = ctx.client.contacts()
    lines: list[str] = []
    results: list[dict[str, Any]] = []
    for reference in references:
        try:
            contact = _find(contacts, reference)
            lines.append(action(contact))
            results.append({"contact": contact.id, "name": contact.shown, "ok": True})
        except BriarError as error:
            lines.append(f"{reference}: {error}")
            results.append({"contact": reference, "ok": False, "error": str(error)})
    return Outcome({key: results}, lines, failed=any(not r["ok"] for r in results))


def _matches(contacts: list[Contact], reference: str) -> list[Contact]:
    if reference.isdecimal():
        return [c for c in contacts if c.id == int(reference)]
    return [c for c in contacts if reference in (c.alias, c.name)]


def _find(contacts: list[Contact], reference: str) -> Contact:
    """A contact by id, or by its alias or name when that is unambiguous."""
    matches = _matches(contacts, reference)
    if not matches:
        raise NotFoundError(f"no contact {reference!r} (`briarctl contact list`)")
    if len(matches) > 1:
        ids = ", ".join(str(c.id) for c in matches)
        raise UsageError(f"{reference!r} names several contacts ({ids}): use the id")
    return matches[0]


def _group(ctx: Context, group_id: str) -> Group:
    for group in ctx.client.groups():
        if group.id == group_id:
            return group
    raise NotFoundError("the hub is in no such group (`briarctl group list`)")


def _contact_line(contact: Contact) -> str:
    name = f"{contact.alias} ({contact.name})" if contact.alias else contact.name
    state = "connected" if contact.connected else "offline"
    verified = "verified" if contact.verified else "unverified"
    return f"{contact.id}  {name}  {state}  {verified}"


def _group_line(group: Group) -> str:
    role = "created by the hub" if group.ours else f"created by {group.creator}"
    flag = "  DISSOLVED" if group.dissolved else ""
    return f"{group.id}  {group.name}  {role}{flag}"
