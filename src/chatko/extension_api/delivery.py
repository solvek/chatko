"""How a delivery ends, as the extension tells the hub and the hub tells the source's extension
(docs/architecture.md §3).
"""

from dataclasses import dataclass
from datetime import timedelta

from chatko.domain import EndpointRef


@dataclass(frozen=True, slots=True)
class Delivered:
    """The endpoint (or the recipient) has the message. `truncated`: only part of the text fit."""

    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Retry:
    """Not delivered now, but it may work later: the network is down, busy, or did not confirm.

    The hub tries again after `after`, or after its own backoff when `after` is `None`, and holds
    back newer messages to the same endpoint and recipient until then, so that they keep their
    order. `HubContext.retry_now` ends the wait early.
    """

    reason: str
    after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.after is not None and self.after < timedelta(0):
            raise ValueError("a retry cannot be due in the past")


@dataclass(frozen=True, slots=True)
class Failed:
    """Not delivered, and trying again cannot help (the chat is gone, the hub was removed)."""

    reason: str


type DeliveryResult = Delivered | Retry | Failed


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """How a message from one of the extension's endpoints fared at one target, once that delivery
    ended. The extension may show it in its network, e.g. a ✂️ reaction for `truncated`.
    """

    source: EndpointRef
    transport_id: str
    target: EndpointRef
    recipient: str | None
    result: Delivered | Failed
