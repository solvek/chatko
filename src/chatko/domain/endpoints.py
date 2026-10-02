"""Endpoints and groups (docs/design.md §2)."""

from dataclasses import dataclass

from chatko.domain.errors import DomainError


@dataclass(frozen=True, slots=True, order=True)
class EndpointRef:
    """One place where an extension instance reads and posts messages: a site or a source.

    `instance` is the extension instance's name from the config (`telegram`, `kyiv`). `name` is the
    name the admin gives the endpoint in the config, unique in the installation: `<group>.<site>`
    for a site (`family.telegram`), the source's own name for a source (`longfast`) (D34). The core
    never reads the endpoint's own settings (a chat id, a channel): only its extension does. The
    extension does not know whether the endpoint is a site or a source: groups are a core concept.
    """

    instance: str
    name: str

    def __post_init__(self) -> None:
        if not self.instance.strip():
            raise DomainError("an endpoint needs an extension instance")
        if not self.name.strip():
            raise DomainError(f"an endpoint of {self.instance!r} needs a name")

    def __str__(self) -> str:
        return f"{self.instance}/{self.name}"


@dataclass(frozen=True, slots=True)
class Group:
    """An independent chat room: a named set of sites, in the order of the config."""

    name: str
    sites: tuple[EndpointRef, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainError("a group needs a name")
        if not self.sites:
            raise DomainError(f"group {self.name!r} has no sites")
        if len(set(self.sites)) != len(self.sites):
            raise DomainError(f"group {self.name!r} lists a site twice")

    def has_site(self, endpoint: EndpointRef) -> bool:
        return endpoint in self.sites

    def other_sites(self, endpoint: EndpointRef) -> tuple[EndpointRef, ...]:
        """All sites except `endpoint`: where the default router sends a message from it."""
        return tuple(site for site in self.sites if site != endpoint)
