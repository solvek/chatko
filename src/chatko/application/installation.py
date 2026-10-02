"""What exists in the running installation, as one consistent snapshot."""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from chatko.domain import EndpointRef, Topology
from chatko.extension_api import EndpointProvider, Extension


@dataclass(frozen=True, slots=True)
class Installation:
    """The topology and the extension instances, by instance name.

    `extensions` are the instances of the config, constructed and given their endpoints;
    `running` names those that are started (`None`: all of them). Only a running instance is
    given deliveries and reports, but the recipients and types of all are known, so that a
    message routed while an instance (re)starts gets its rows. `fingerprint_dedup` gives the
    endpoints whose messages are routed only if no message with the same fingerprint arrived
    within the window before (design.md §9.3). `admin_endpoint` is where admin notices go
    (`None`: they are only logged). The `HubRuntime` makes a new snapshot when the config or the
    running instances change; each message, admin notices included, is handled with one snapshot
    from start to end.
    """

    topology: Topology
    extensions: Mapping[str, Extension[Any]] = field(default_factory=dict)
    fingerprint_dedup: Mapping[EndpointRef, timedelta] = field(default_factory=dict)
    running: Collection[str] | None = None
    admin_endpoint: EndpointRef | None = None

    def __post_init__(self) -> None:
        if self.admin_endpoint is not None and not self.topology.has_endpoint(self.admin_endpoint):
            raise ValueError(f"the admin endpoint {self.admin_endpoint} is not in the topology")
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        object.__setattr__(
            self, "fingerprint_dedup", MappingProxyType(dict(self.fingerprint_dedup))
        )
        running = self.extensions.keys() if self.running is None else self.running
        object.__setattr__(self, "running", frozenset(running))
        for endpoint, window in self.fingerprint_dedup.items():
            if window <= timedelta(0):
                raise ValueError(f"the de-duplication window of {endpoint} must be positive")

    def is_running(self, instance: str) -> bool:
        return self.running is not None and instance in self.running

    def provider(self, instance: str) -> EndpointProvider[Any] | None:
        """The running instance with this name, if it provides endpoints."""
        return self._provider(instance) if self.is_running(instance) else None

    def _provider(self, instance: str) -> EndpointProvider[Any] | None:
        # `object`: mypy takes an `Extension` that is also an `EndpointProvider` to be impossible.
        extension: object = self.extensions.get(instance)
        return extension if isinstance(extension, EndpointProvider) else None

    @property
    def extension_types(self) -> Mapping[str, str]:
        """Each instance's extension type: `telegram` → `telegram`."""
        return {name: extension.type_name for name, extension in self.extensions.items()}

    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]:
        """The recipients of the endpoint, as its extension lists them, whether it runs or not;
        `()` if it has none or there is no such instance."""
        provider = self._provider(endpoint.instance)
        return () if provider is None else provider.recipients(endpoint)

    def all_recipients(self) -> Mapping[EndpointRef, tuple[str, ...]]:
        """The recipients of every endpoint of the topology that has some."""
        found = {endpoint: self.recipients(endpoint) for endpoint in self.topology.endpoints}
        return {endpoint: names for endpoint, names in found.items() if names}


type InstallationSource = Callable[[], Installation]
"""Gives the current snapshot. The services ask for it once per message or delivery."""
