"""The one exception type of the domain."""


class DomainError(ValueError):
    """A value breaks a rule of the domain, e.g. an empty name or an endpoint in two groups."""
