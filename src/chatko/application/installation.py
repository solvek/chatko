"""What exists in the running installation, as one consistent snapshot."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from chatko.domain import EndpointRef, Topology
from chatko.extension_api import EndpointProvider, Extension


@dataclass(frozen=True, slots=True)
class Installation:
    """The topology and the running extension instances, by instance name.

    `fingerprint_dedup` gives the endpoints whose messages are routed only if no message with the
    same fingerprint arrived within the window before (design.md §9.3). The composition root
    makes a new snapshot when the config or the running instances change; each message is handled
    with one snapshot from start to end.
    """

    topology: Topology
    extensions: Mapping[str, Extension[Any]] = field(default_factory=dict)
    fingerprint_dedup: Mapping[EndpointRef, timedelta] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extensions", MappingProxyType(dict(self.extensions)))
        object.__setattr__(
            self, "fingerprint_dedup", MappingProxyType(dict(self.fingerprint_dedup))
        )
        for endpoint, window in self.fingerprint_dedup.items():
            if window <= timedelta(0):
                raise ValueError(f"the de-duplication window of {endpoint} must be positive")

    def provider(self, instance: str) -> EndpointProvider[Any] | None:
        """The running instance with this name, if it provides endpoints."""
        # `object`: mypy takes an `Extension` that is also an `EndpointProvider` to be impossible.
        extension: object = self.extensions.get(instance)
        return extension if isinstance(extension, EndpointProvider) else None

    @property
    def extension_types(self) -> Mapping[str, str]:
        """Each running instance's extension type: `tg` → `telegram`."""
        return {name: extension.type_name for name, extension in self.extensions.items()}

    def recipients(self, endpoint: EndpointRef) -> tuple[str, ...]:
        """The recipients of the endpoint, as its extension lists them; `()` if it has none or
        its instance is not running."""
        provider = self.provider(endpoint.instance)
        return () if provider is None else provider.recipients(endpoint)

    def all_recipients(self) -> Mapping[EndpointRef, tuple[str, ...]]:
        """The recipients of every endpoint of the topology that has some."""
        found = {endpoint: self.recipients(endpoint) for endpoint in self.topology.endpoints}
        return {endpoint: names for endpoint, names in found.items() if names}


type InstallationSource = Callable[[], Installation]
"""Gives the current snapshot. The services ask for it once per message or delivery."""
