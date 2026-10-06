"""The admin notice for an account the hub sees for the first time (docs/design.md §8)."""

import logging
from collections.abc import Callable

from chatko.application.ports import AccountRegistry, AdminNotices, Clock
from chatko.domain import Account, EndpointRef, Topology

_log = logging.getLogger("chatko.accounts")


class NewAccounts:
    """Notes every account the extensions show the hub, and tells the admin of a new one.

    Every account that posts or is heard is noted in the `AccountRegistry` with its latest names.
    When it shows up at a site of a group for the first time, `enabled()` is true (`admin_notices.
    new_accounts`) and the account belongs to no person of the config, the admin gets a notice
    with the account's key and names, so that it can be added to `people`. An account seen only
    elsewhere (a source such as a mesh's public channel, a packet at no endpoint) gets none: there
    can be hundreds (D66). It never raises.
    """

    def __init__(
        self,
        *,
        registry: AccountRegistry,
        notices: AdminNotices,
        clock: Clock,
        topology: Callable[[], Topology],
        enabled: Callable[[], bool],
    ) -> None:
        self._registry = registry
        self._notices = notices
        self._clock = clock
        self._topology = topology
        self._enabled = enabled

    async def saw(self, account: Account, endpoint: EndpointRef | None = None) -> None:
        """An extension showed the hub this account, at `endpoint` if at one."""
        try:
            topology = self._topology()
            at_site = endpoint is not None and topology.group_of(endpoint) is not None
            site = endpoint if at_site else None
            first = await self._registry.note(account, self._clock.now(), at_site=at_site)
            if site is None or not first or not self._enabled():
                return
            if topology.person_of(account.key) is not None:
                return
            names = " / ".join(
                dict.fromkeys(name for name in (account.display_name, account.short_name) if name)
            )
            await self._notices.notify(
                f"A new account at {site.name}: {account.key}"
                + (f" ({names})" if names else "")
                + ". To give it a label of its own, add it to a person in `people`.",
                key=f"account:{account.key}",
            )
        except Exception:
            _log.exception("noting the account %s failed", account.key)
