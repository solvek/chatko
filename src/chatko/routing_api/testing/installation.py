"""A fake installation for testing a routing script with plain `pytest` (docs/design.md §9.5)."""

import itertools
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from chatko.domain import (
    Account,
    AccountKey,
    Attachment,
    AttachmentKind,
    Author,
    EndpointRef,
    Fingerprint,
    Group,
    Message,
    MessageId,
    Person,
    Target,
    Topology,
    plain_text,
)
from chatko.routing_api.context import RoutingContext
from chatko.routing_api.helpers import default_label
from chatko.routing_api.messages import RoutedMessage
from chatko.routing_api.script import RoutingScript
from chatko.routing_api.testing.history import FakeHistory

DEFAULT_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Outgoing:
    """What the script asked for at one endpoint: the label and text the message would go out
    with, and the recipients it would reach (`()` at an endpoint without recipients).

    `formatted` is the message as a text-only network shows it: `NatAda: [photo] caption`.
    """

    endpoint: str
    label: str
    text: str
    recipients: tuple[str, ...]
    formatted: str
    target: Target


class RouteResult:
    """The targets a script returned for a message, labelled, in the script's order.

    It shows what the script asked for. The hub then applies its invariants (design.md §9.3):
    nothing goes back to where the message came from or to an endpoint that is not in the
    config, and each endpoint and recipient gets the message once.
    """

    def __init__(self, outgoing: Iterable[Outgoing]) -> None:
        self._outgoing = tuple(outgoing)

    @property
    def endpoints(self) -> list[str]:
        """The names of the endpoints, in the script's order: `["family.briar", "family.tg"]`."""
        return [out.endpoint for out in self._outgoing]

    def to(self, endpoint: str) -> Outgoing:
        """The first target at the endpoint with this name; `KeyError` if there is none."""
        for out in self._outgoing:
            if out.endpoint == endpoint:
                return out
        raise KeyError(f"no target at {endpoint!r}; the targets are {self.endpoints}")

    def __len__(self) -> int:
        return len(self._outgoing)

    def __iter__(self) -> Iterator[Outgoing]:
        return iter(self._outgoing)

    def __repr__(self) -> str:
        return f"RouteResult({self.endpoints!r})"


def assert_routed_to(result: RouteResult, *endpoints: str) -> None:
    """Assert that the script sent the message to exactly these endpoints, in any order; with
    none, that it sent it nowhere."""
    expected, actual = set(endpoints), set(result.endpoints)
    if expected == actual:
        return
    problems = []
    if missing := sorted(expected - actual):
        problems.append(f"not to {', '.join(missing)}")
    if unexpected := sorted(actual - expected):
        problems.append(f"also to {', '.join(unexpected)}")
    raise AssertionError(f"routed {'; '.join(problems)}: the targets are {result.endpoints}")


class FakeInstallation:
    """An installation described as in `chatko.yaml`, for testing a routing script.

    ```python
    hub = FakeInstallation(
        extensions={"tg": "telegram", "kyiv": "meshtastic"},
        groups={"family": {"tg": "tg", "radio": "kyiv"}},     # site name: extension instance
        sources={"longfast": "kyiv"},
        recipients={"family.radio": ["!a1b2c3d4", "!0badc0de"]},
        people={"NatAda": ["telegram:111", "meshtastic:!a1b2c3d4"]},
    )
    msg = hub.message("family.tg", "Привіт", author="telegram:111", name="Наталія Адамчук")
    result = hub.route(routing, msg)                           # routing: the script's module
    assert result.endpoints == ["family.radio"]
    assert result.to("family.radio").label == "NatAda"
    ```

    The clock stands at `now` until a test changes it. `hear` and `see` fill the history that
    `ctx.last_heard` and `ctx.seen` read.
    """

    def __init__(
        self,
        *,
        extensions: Mapping[str, str],
        groups: Mapping[str, Mapping[str, str]] | None = None,
        sources: Mapping[str, str] | None = None,
        recipients: Mapping[str, Iterable[str]] | None = None,
        people: Mapping[str, Iterable[str]] | None = None,
        now: datetime = DEFAULT_NOW,
    ) -> None:
        self.extensions = dict(extensions)
        self.topology = Topology(
            groups=tuple(
                Group(
                    group,
                    tuple(
                        self._endpoint(f"{group}.{site}", instance)
                        for site, instance in sites.items()
                    ),
                )
                for group, sites in (groups or {}).items()
            ),
            sources={
                name: self._endpoint(name, instance) for name, instance in (sources or {}).items()
            },
            people=tuple(
                Person(label, frozenset(AccountKey.parse(account) for account in accounts))
                for label, accounts in (people or {}).items()
            ),
        )
        self.recipients = {
            self.endpoint(name): tuple(nodes) for name, nodes in (recipients or {}).items()
        }
        self.history = FakeHistory()
        self.now = now
        self._ids = itertools.count(1)

    def endpoint(self, name: str) -> EndpointRef:
        """The site or source with this name; `KeyError` if there is none."""
        return self.topology.endpoint(name)

    def context(self) -> RoutingContext:
        """The context the hub would route with now."""
        return RoutingContext(
            self.topology,
            now=self.now,
            extension_types=self.extensions,
            recipients=self.recipients,
            history=self.history,
        )

    def message(
        self,
        endpoint: str,
        text: str,
        *,
        author: str | None = None,
        name: str = "",
        short_name: str | None = None,
        attachments: Iterable[AttachmentKind] = (),
        from_recipient: str | None = None,
        relayed_label: str | None = None,
    ) -> RoutedMessage:
        """A message arriving now at the endpoint with this name.

        `author` is the account as in `people` (`telegram:111`); by default an account of the
        endpoint's extension type that is not in `people`. `name` and `short_name` are the names
        the network gives it. `from_recipient` is the recipient of the endpoint that posted it
        (the node of a `dm` endpoint), and `relayed_label` marks a peer hub's relay.
        """
        where = self.endpoint(endpoint)
        number = next(self._ids)
        key = (
            AccountKey(self.extensions[where.instance], f"someone{number}")
            if author is None
            else AccountKey.parse(author)
        )
        account = Account(key, name, short_name)
        person = None if relayed_label is not None else self.topology.person_of(key)
        stored = Message(
            MessageId(f"m{number}"),
            where,
            f"t{number}",
            Author(account, person, relayed_label),
            text,
            self.now,
            tuple(Attachment(kind) for kind in attachments),
            from_recipient,
        )
        return RoutedMessage(stored, self.topology.group_of(where))

    def hear(
        self, account: str, *, endpoint: str | None = None, ago: timedelta = timedelta(0)
    ) -> None:
        """The hub heard `account` (`meshtastic:!a1b2c3d4`) `ago` before now, at the endpoint with
        this name if given."""
        where = None if endpoint is None else self.endpoint(endpoint)
        self.history.hear(AccountKey.parse(account), self.now - ago, where)

    def see(self, message: RoutedMessage | Fingerprint, *, ago: timedelta = timedelta(0)) -> None:
        """A message (or one with this fingerprint) arrived `ago` before now."""
        fingerprint = message.fingerprint if isinstance(message, RoutedMessage) else message
        self.history.see(fingerprint, self.now - ago)

    def route(self, script: object, msg: RoutedMessage) -> RouteResult:
        """Route the message with the script as the hub would: check the script, call its
        `route`, and label each target with its own label, the script's `label` or
        `default_label`. The script's errors are raised, so that a test shows them."""
        functions = RoutingScript.of(script)
        ctx = self.context()
        outgoing = []
        for target in functions.route(msg, ctx):
            if target.label is not None:
                label = target.label
            elif functions.label is not None:
                label = functions.label(msg, target, ctx)
            else:
                label = default_label(msg.author, ctx)
            text = msg.text if target.text is None else target.text
            recipients = self.recipients.get(target.endpoint, ())
            outgoing.append(
                Outgoing(
                    target.endpoint.name,
                    label,
                    text,
                    tuple(node for node in recipients if target.includes(node)),
                    f"{label}: {plain_text(text, msg.attachments)}",
                    target,
                )
            )
        return RouteResult(outgoing)

    def _endpoint(self, name: str, instance: str) -> EndpointRef:
        if instance not in self.extensions:
            raise KeyError(f"{name} uses extension instance {instance!r}, which is not listed")
        return EndpointRef(instance, name)
