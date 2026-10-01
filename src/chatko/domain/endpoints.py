"""Endpoints and groups (docs/design.md §2)."""

from dataclasses import dataclass

from chatko.domain.errors import DomainError


@dataclass(frozen=True, slots=True, order=True)
class EndpointRef:
    """One place where an extension instance reads and posts messages: a leg or a source.

    `instance` is the extension instance's name from the config (`tg`, `kyiv`). `name` is unique
    within that instance and stable across restarts. The extension does not know whether the
    endpoint is a leg or a source: groups are a core concept.
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
    """An independent chat room: a named set of legs, in the order of the config."""

    name: str
    legs: tuple[EndpointRef, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainError("a group needs a name")
        if not self.legs:
            raise DomainError(f"group {self.name!r} has no legs")
        if len(set(self.legs)) != len(self.legs):
            raise DomainError(f"group {self.name!r} lists a leg twice")

    def has_leg(self, endpoint: EndpointRef) -> bool:
        return endpoint in self.legs

    def other_legs(self, endpoint: EndpointRef) -> tuple[EndpointRef, ...]:
        """All legs except `endpoint`: where the default router sends a message from it."""
        return tuple(leg for leg in self.legs if leg != endpoint)
