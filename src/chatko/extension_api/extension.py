"""The base class of every extension (docs/architecture.md §3)."""

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from chatko.extension_api.hub import HubContext


class Extension[C: BaseModel](ABC):
    """One configured instance of an extension: it connects the hub to one network.

    A subclass sets the three class attributes, implements `start` and `stop`, and provides
    endpoints by also deriving from `EndpointProvider`. It is registered in the `chatko.extensions`
    entry point group under its `type_name` (docs/architecture.md §2).

    The core runs an instance through these steps (docs/architecture.md §3.1): the constructor and
    `set_endpoints`, which must not do I/O or start tasks; `start`; deliveries and calls into the
    hub; `stop`. When the instance's own config section changes, the core stops it and starts a
    new instance.
    """

    type_name: ClassVar[str]
    """The extension's name in the config (`type: telegram`) and the kind of its accounts."""

    api_version: ClassVar[tuple[int, int]]
    """The version of this API the extension was written for: see `is_supported`."""

    config_model: ClassVar[type[BaseModel]]
    """Validates the instance's config section, without `type`. It should reject unknown keys."""

    def __init__(self, instance: str, config: C, hub: HubContext) -> None:
        self.instance = instance
        self.config = config
        self.hub = hub
        self.logger = logging.getLogger(f"chatko.extensions.{instance}")

    @abstractmethod
    async def start(self) -> None:
        """Start the background work (connections, polling) and return.

        Do not wait for the network to be reachable: reconnect in the background, and report
        lasting trouble with `HubContext.notify_admin`. Raise only when the instance cannot work
        at all; the core then logs it, tells the admin and keeps the other instances running.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop all background work and close the connections. No calls into the hub follow.

        The core calls it after the deliveries in progress have ended. It must be safe to call
        even if `start` was not called or failed.
        """
