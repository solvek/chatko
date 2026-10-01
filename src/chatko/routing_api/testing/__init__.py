"""Test kit for routing scripts: a fake installation, its history and assertions (design.md §9.5).

The admin tests `config/routing.py` with plain `pytest`, next to the script; `routing.example.py`
is tested the same way in chatko's CI.
"""

from chatko.routing_api.testing.history import FakeHistory
from chatko.routing_api.testing.installation import (
    DEFAULT_NOW,
    FakeInstallation,
    Outgoing,
    RouteResult,
    assert_routed_to,
)

__all__ = [
    "DEFAULT_NOW",
    "FakeHistory",
    "FakeInstallation",
    "Outgoing",
    "RouteResult",
    "assert_routed_to",
]
