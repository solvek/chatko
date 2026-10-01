"""The version of the routing API (docs/architecture.md §3.4, §4)."""

from typing import Final

API_VERSION: Final = (1, 0)
"""The version of this API: (major, minor). A minor version only adds; a major one breaks."""


def is_supported(version: tuple[int, int]) -> bool:
    """Whether a script written for API `version` runs on this one: the same major version, and
    a minor version no newer than this one's.
    """
    major, minor = version
    return major == API_VERSION[0] and minor <= API_VERSION[1]
