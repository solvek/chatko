"""The message as the routing script sees it (docs/architecture.md §4)."""

from datetime import datetime

from chatko.domain import Attachment, Author, EndpointRef, Fingerprint, Group, Message, MessageId


class RoutedMessage:
    """An incoming message, read-only, with the group of its endpoint.

    The core makes it from the stored message (design.md §9.1, step 2). `group` is `None` when
    the message came from a source.
    """

    __slots__ = ("_fingerprint", "_group", "_message")

    def __init__(self, message: Message, group: Group | None) -> None:
        if group is not None and not group.has_site(message.endpoint):
            raise ValueError(f"{message.endpoint} is not a site of group {group.name!r}")
        self._message = message
        self._group = group
        self._fingerprint = message.fingerprint

    @property
    def id(self) -> MessageId:
        return self._message.id

    @property
    def endpoint(self) -> EndpointRef:
        """Where the message came from: a site or a source."""
        return self._message.endpoint

    @property
    def group(self) -> Group | None:
        return self._group

    @property
    def author(self) -> Author:
        return self._message.author

    @property
    def text(self) -> str:
        """The text, or the caption of an attachment; may be empty then."""
        return self._message.text

    @property
    def attachments(self) -> tuple[Attachment, ...]:
        return self._message.attachments

    @property
    def plain_text(self) -> str:
        """The text as a text-only network shows it: `[photo] caption`."""
        return self._message.plain_text

    @property
    def fingerprint(self) -> Fingerprint:
        """The same for copies of one message that came by different paths (design.md §9.5)."""
        return self._fingerprint

    @property
    def received_at(self) -> datetime:
        return self._message.received_at

    def __repr__(self) -> str:
        return f"RoutedMessage({self.id!r} from {self.endpoint})"
