"""`FakeBriarClient`: `briar-headless`'s contacts and private groups in memory, for tests.

It follows the API's rules (design.md §7.4): only the creator lists and sends invitations, only a
`shareable` contact can be invited, only a member can be revealed, an unknown group, contact or
invitation is `NotFoundError`. The `add_*`, `receive_*` and `join` methods play what other people
do in their Briar apps; `fail` makes every call raise.
"""

from dataclasses import dataclass, field, replace

from briarctl import ids
from briarctl.api import (
    BriarError,
    Contact,
    Group,
    Invitation,
    Member,
    NotFoundError,
    PendingContact,
    Sharing,
    rejection,
)

OWN_NAME = "chatko"
OWN_LINK = "briar://hubhubhubhubhubhubhubhubhubhubhubhubhubhubhubhubhubhubhubhubhub"


@dataclass
class _Record:
    group: Group
    members: list[Member]
    sharing: dict[int, str] = field(default_factory=dict)
    """Status of the contacts that are not `shareable`, for a group the hub created."""


class FakeBriarClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        """Every call, as its name and arguments."""
        self.fail: BriarError | None = None
        """Raised by every call while it is set."""
        self._contacts: dict[int, Contact] = {}
        self._pending: dict[str, PendingContact] = {}
        self._links: dict[str, str] = {}
        self._invitations: dict[str, Invitation] = {}
        self._creators: dict[str, Contact] = {}
        self._groups: dict[str, _Record] = {}
        self._next = 1

    # What other people do.

    def add_contact_record(self, name: str, alias: str | None = None) -> Contact:
        """A contact the hub has made, as if both sides had added each other's link."""
        number = self._number()
        contact = Contact(number, name, alias, ids.from_bytes(self._raw(number)), True, False)
        self._contacts[number] = contact
        return contact

    def receive_invitation(self, name: str, creator: Contact) -> str:
        """A contact invites the hub to a group they made; returns the group's id."""
        group_id = ids.from_bytes(self._raw(self._number()))
        self._invitations[group_id] = Invitation(group_id, name, creator.name, creator.id)
        self._creators[group_id] = creator
        return group_id

    def join(self, group_id: str, contact: Contact) -> None:
        """A contact the hub invited accepts, and becomes a member of the group."""
        record = self._groups[group_id]
        record.sharing[contact.id] = "sharing"
        record.members.append(Member(contact.author_id, contact.name, False, contact.id, "visible"))

    def add_member(self, group_id: str, name: str, contact: Contact | None = None) -> Member:
        """Someone else joins a group the hub is in; a contact's relationship is not revealed."""
        author = contact.author_id if contact else ids.from_bytes(self._raw(self._number()))
        member = Member(author, name, False, contact.id if contact else None, "invisible")
        self._groups[group_id].members.append(member)
        return member

    def dissolve(self, group_id: str) -> None:
        """The creator of a group the hub joined dissolves it."""
        record = self._groups[group_id]
        record.group = replace(record.group, dissolved=True)

    # The port.

    def link(self) -> str:
        self._record("link")
        return OWN_LINK

    def contacts(self) -> list[Contact]:
        self._record("contacts")
        return list(self._contacts.values())

    def pending_contacts(self) -> list[PendingContact]:
        self._record("pending_contacts")
        return list(self._pending.values())

    def add_contact(self, link: str, alias: str) -> None:
        self._record("add_contact", link, alias)
        if not link.startswith("briar://") or len(link) <= len("briar://"):
            raise rejection("INVALID_LINK")
        if link in self._links:
            raise rejection("PENDING_EXISTS", self._links[link])
        pending = PendingContact(
            ids.from_bytes(self._raw(self._number())), alias, "waiting_for_connection"
        )
        self._pending[pending.id] = pending
        self._links[link] = alias

    def remove_contact(self, contact_id: int) -> None:
        self._record("remove_contact", contact_id)
        if self._contacts.pop(contact_id, None) is None:
            raise NotFoundError("no such contact")

    def cancel_pending(self, pending_id: str) -> None:
        self._record("cancel_pending", pending_id)
        pending = self._pending.pop(pending_id, None)
        if pending is None:
            raise NotFoundError("no such pending contact")
        self._links = {link: alias for link, alias in self._links.items() if alias != pending.alias}

    def invitations(self) -> list[Invitation]:
        self._record("invitations")
        return list(self._invitations.values())

    def answer_invitation(self, group_id: str, *, accept: bool) -> None:
        self._record("answer_invitation", group_id, accept)
        invitation = self._invitations.pop(group_id, None)
        if invitation is None:
            raise NotFoundError("no such invitation")
        creator = self._creators.pop(group_id)
        if accept:
            group = Group(group_id, invitation.name, creator.name, False, False)
            members = [
                Member(creator.author_id, creator.name, True, creator.id, "visible"),
                Member(ids.from_bytes(self._raw(0)), OWN_NAME, False, None, "visible"),
            ]
            self._groups[group_id] = _Record(group, members)

    def groups(self) -> list[Group]:
        self._record("groups")
        return [record.group for record in self._groups.values()]

    def create_group(self, name: str) -> Group:
        self._record("create_group", name)
        group_id = ids.from_bytes(self._raw(self._number()))
        group = Group(group_id, name, OWN_NAME, True, False)
        own = Member(ids.from_bytes(self._raw(0)), OWN_NAME, True, None, "visible")
        self._groups[group_id] = _Record(group, [own])
        return group

    def leave_group(self, group_id: str) -> None:
        self._record("leave_group", group_id)
        self._group(group_id)
        del self._groups[group_id]

    def members(self, group_id: str) -> list[Member]:
        self._record("members", group_id)
        return list(self._group(group_id).members)

    def reveal(self, group_id: str, contact_id: int) -> None:
        self._record("reveal", group_id, contact_id)
        record = self._group(group_id)
        self._contact(contact_id)
        for index, member in enumerate(record.members):
            if member.contact_id == contact_id:
                if member.visibility == "invisible":
                    record.members[index] = replace(member, visibility="revealed_by_us")
                return
        raise rejection("NOT_MEMBER")

    def sharing(self, group_id: str) -> list[Sharing]:
        self._record("sharing", group_id)
        record = self._creator_group(group_id)
        return [
            Sharing(contact_id, record.sharing.get(contact_id, "shareable"))
            for contact_id in self._contacts
        ]

    def invite(self, group_id: str, contact_id: int, text: str | None) -> Sharing:
        self._record("invite", group_id, contact_id, text)
        record = self._creator_group(group_id)
        self._contact(contact_id)
        status = record.sharing.get(contact_id, "shareable")
        if status != "shareable":
            raise rejection("NOT_SHAREABLE", status)
        record.sharing[contact_id] = "invite_sent"
        return Sharing(contact_id, "invite_sent")

    # Internals.

    def _record(self, name: str, *arguments: object) -> None:
        self.calls.append((name, *arguments))
        if self.fail is not None:
            raise self.fail

    def _number(self) -> int:
        number = self._next
        self._next += 1
        return number

    @staticmethod
    def _raw(number: int) -> bytes:
        return number.to_bytes(ids.ID_SIZE, "big")

    def _group(self, group_id: str) -> _Record:
        try:
            return self._groups[group_id]
        except KeyError:
            raise NotFoundError("no such group") from None

    def _creator_group(self, group_id: str) -> _Record:
        record = self._group(group_id)
        if not record.group.ours:
            raise rejection("NOT_CREATOR")
        return record

    def _contact(self, contact_id: int) -> Contact:
        try:
            return self._contacts[contact_id]
        except KeyError:
            raise NotFoundError("no such contact") from None
