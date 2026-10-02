"""The core's configuration: the models of `chatko.yaml` and its validation (docs/design.md §10).

`validate_config` turns the parsed YAML (after `${ENV}` substitution) into a `Config`, or raises a
`ConfigError` that lists every problem it found. It checks the core's own sections, hands each
extension instance's section and each endpoint's settings to the extension's pydantic models, and
runs the constructor and `set_endpoints` of every instance on a fresh, unstarted object, which is
how an extension rejects what its models cannot (a channel the node does not have).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chatko.domain import AccountKey, DomainError, EndpointRef, Group, Person, Topology
from chatko.extension_api import Account, EndpointProvider, Extension, HubContext, InboundMessage

type ExtensionTypes = Mapping[str, type[Extension[Any]]]
"""The installed extension classes, by `type_name`."""

DEFAULT_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650


class ConfigError(Exception):
    """The config is not valid. `errors` has one line per problem, each starting with the place in
    the config it is about (`groups.family.sites.telegram`). They never contain the config's
    values: a value may be a secret."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("invalid config:\n" + "\n".join(f"- {error}" for error in self.errors))


@dataclass(frozen=True, slots=True)
class ExtensionSetup:
    """One extension instance as configured: its class and its validated config."""

    type_name: str
    config: BaseModel


@dataclass(frozen=True, slots=True)
class Config:
    """A valid `chatko.yaml`.

    `extensions` and `endpoints` are in the order of the config; `endpoints` has an entry for every
    instance, empty for one without endpoints. An instance whose `ExtensionSetup` changed has to be
    restarted (architecture.md §3.1); one whose endpoints changed is given the new set.
    """

    extensions: Mapping[str, ExtensionSetup]
    endpoints: Mapping[str, Mapping[EndpointRef, BaseModel]]
    topology: Topology
    fingerprint_dedup: Mapping[EndpointRef, timedelta]
    admin_endpoint: EndpointRef | None
    """Where admin notices are posted; `None`: nowhere, they are only logged."""
    new_accounts: bool
    """Whether a notice is posted for every account seen for the first time (design.md §8)."""
    routing: str | None
    """The routing script's path as written, relative to the config file; `None`: the defaults."""
    retention: timedelta
    """How long messages and fingerprints are kept (design.md §10)."""
    peers: Mapping[str, frozenset[AccountKey]]
    """The other hubs' accounts. Parsed and checked, not used yet (design.md §9.6)."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _AdminNotices(_Section):
    to: str = Field(min_length=1)
    new_accounts: bool = False


class _Group(_Section):
    sites: dict[str, dict[str, Any]] = Field(min_length=1)


class _Core(_Section):
    extensions: dict[str, dict[str, Any]] = {}
    groups: dict[str, _Group] = {}
    sources: dict[str, dict[str, Any]] = {}
    admin_notices: _AdminNotices | None = None
    people: dict[str, list[str]] = {}
    routing: str | None = Field(default=None, min_length=1)
    fingerprint_dedup_s: dict[str, float] = {}
    retention_days: int = Field(default=DEFAULT_RETENTION_DAYS, ge=1, le=MAX_RETENTION_DAYS)
    peers: dict[str, list[str]] = {}


def validate_config(raw: Mapping[str, Any], types: ExtensionTypes) -> Config:
    """Check `raw` against the core's models and the installed extensions. See the module."""
    try:
        core = _Core.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(_describe("", error)) from None
    return _Validator(core, types).run()


class _Validator:
    def __init__(self, core: _Core, types: ExtensionTypes) -> None:
        self._core = core
        self._types = types
        self._errors: list[str] = []
        self._setups: dict[str, ExtensionSetup] = {}
        self._endpoints: dict[str, dict[EndpointRef, BaseModel]] = {}

    def run(self) -> Config:
        core = self._core
        self._extensions()
        people = self._people()
        peers = self._peers()
        sources, groups = self._sites()
        if self._errors:
            raise ConfigError(self._errors)
        topology = self._topology(groups, sources, people)
        dedup = self._dedup(topology)
        admin = self._admin(topology)
        if not self._errors:
            self._try_endpoints()
        if self._errors:
            raise ConfigError(self._errors)
        return Config(
            extensions=self._setups,
            endpoints=self._endpoints,
            topology=topology,
            fingerprint_dedup=dedup,
            admin_endpoint=admin,
            new_accounts=core.admin_notices is not None and core.admin_notices.new_accounts,
            routing=core.routing,
            retention=timedelta(days=core.retention_days),
            peers=peers,
        )

    def _error(self, where: str, problem: str) -> None:
        self._errors.append(f"{where}: {problem}")

    def _extensions(self) -> None:
        for name, section in self._core.extensions.items():
            where = f"extensions.{name}"
            type_name = section.get("type")
            if not isinstance(type_name, str):
                self._error(where, "`type` is required and names the extension")
                continue
            cls = self._types.get(type_name)
            if cls is None:
                known = ", ".join(sorted(self._types)) or "none"
                self._error(where, f"unknown extension type {type_name!r}; installed: {known}")
                continue
            settings = {key: value for key, value in section.items() if key != "type"}
            try:
                config = cls.config_model.model_validate(settings)
            except ValidationError as error:
                self._errors.extend(_describe(where, error))
                continue
            self._setups[name] = ExtensionSetup(type_name, config)
            self._endpoints[name] = {}

    def _people(self) -> tuple[Person, ...]:
        people = []
        for label, accounts in self._core.people.items():
            where = f"people.{label}"
            try:
                people.append(Person(label, frozenset(AccountKey.parse(a) for a in accounts)))
            except DomainError as error:
                self._error(where, str(error))
        return tuple(people)

    def _peers(self) -> dict[str, frozenset[AccountKey]]:
        peers = {}
        for name, accounts in self._core.peers.items():
            try:
                peers[name] = frozenset(AccountKey.parse(account) for account in accounts)
            except DomainError as error:
                self._error(f"peers.{name}", str(error))
        return peers

    def _sites(self) -> tuple[dict[str, EndpointRef], list[Group]]:
        groups = []
        for group_name, group in self._core.groups.items():
            sites = []
            for site_name, section in group.sites.items():
                name = f"{group_name}.{site_name}"
                endpoint = self._endpoint(f"groups.{group_name}.sites.{site_name}", name, section)
                if endpoint is not None:
                    sites.append(endpoint)
            if len(sites) == len(group.sites):
                try:
                    groups.append(Group(group_name, tuple(sites)))
                except DomainError as error:
                    self._error(f"groups.{group_name}", str(error))
        sources = {}
        for name, section in self._core.sources.items():
            endpoint = self._endpoint(f"sources.{name}", name, section)
            if endpoint is not None:
                sources[name] = endpoint
        return sources, groups

    def _endpoint(self, where: str, name: str, section: Mapping[str, Any]) -> EndpointRef | None:
        instance = section.get("ext")
        if not isinstance(instance, str):
            self._error(where, "`ext` is required and names an extension instance")
            return None
        setup = self._setups.get(instance)
        if setup is None:
            if instance not in self._core.extensions:
                self._error(where, f"`ext` {instance!r} is not an extension instance of the config")
            return None  # the extension's own error is reported already
        provider: type[object] = self._types[setup.type_name]
        if not issubclass(provider, EndpointProvider):
            self._error(where, f"extension {instance!r} ({setup.type_name}) has no endpoints")
            return None
        settings = {key: value for key, value in section.items() if key != "ext"}
        try:
            config = provider.endpoint_config_model.model_validate(settings)
        except ValidationError as error:
            self._errors.extend(_describe(where, error))
            return None
        try:
            endpoint = EndpointRef(instance, name)
        except DomainError as error:
            self._error(where, str(error))
            return None
        self._endpoints[instance][endpoint] = config
        return endpoint

    def _topology(
        self, groups: list[Group], sources: dict[str, EndpointRef], people: tuple[Person, ...]
    ) -> Topology:
        try:
            return Topology(tuple(groups), sources, people)
        except DomainError as error:
            raise ConfigError([*self._errors, str(error)]) from None

    def _dedup(self, topology: Topology) -> dict[EndpointRef, timedelta]:
        window = {}
        retention = self._core.retention_days
        for name, seconds in self._core.fingerprint_dedup_s.items():
            where = f"fingerprint_dedup_s.{name}"
            # `seen` cannot look further back than the fingerprints are kept; NaN fails too.
            if not 0 < seconds <= timedelta(days=retention).total_seconds():
                self._error(
                    where,
                    "the window must be positive and no longer than the retention "
                    f"(retention_days: {retention}), for which fingerprints are kept",
                )
            elif (endpoint := self._known_endpoint(topology, name, where)) is not None:
                window[endpoint] = timedelta(seconds=seconds)
        return window

    def _admin(self, topology: Topology) -> EndpointRef | None:
        admin = self._core.admin_notices
        if admin is None:
            return None
        return self._known_endpoint(topology, admin.to, "admin_notices.to")

    def _known_endpoint(self, topology: Topology, name: str, where: str) -> EndpointRef | None:
        try:
            return topology.endpoint(name)
        except KeyError:
            known = ", ".join(sorted(endpoint.name for endpoint in topology.endpoints))
            self._error(where, f"no site or source {name!r}; the endpoints are: {known or 'none'}")
            return None

    def _try_endpoints(self) -> None:
        """Run the constructor and `set_endpoints` of each instance on a fresh object."""
        for name, setup in self._setups.items():
            where = f"extensions.{name}"
            cls = self._types[setup.type_name]
            try:
                instance = cls(name, setup.config, _InertHub())
                if isinstance(instance, EndpointProvider):
                    instance.set_endpoints(self._endpoints[name])
            except Exception as error:
                self._error(where, f"{type(error).__name__}: {error}")


def _describe(where: str, error: ValidationError) -> list[str]:
    """One line per problem of a pydantic error: its place and message, never the value."""
    lines = []
    for problem in error.errors(include_input=False, include_url=False):
        place = ".".join(str(part) for part in (*([where] if where else []), *problem["loc"]))
        lines.append(f"{place or 'config'}: {problem['msg']}")
    return lines


class _InertHub(HubContext):
    """The hub of an instance that is only checked and never started: no call is expected."""

    async def submit(self, message: InboundMessage) -> None:
        del message
        raise self._unexpected()

    async def heard(self, account: Account, endpoint: EndpointRef | None = None) -> None:
        del account, endpoint
        raise self._unexpected()

    async def retry_now(self, endpoint: EndpointRef, recipient: str | None = None) -> None:
        del endpoint, recipient
        raise self._unexpected()

    async def notify_admin(self, text: str, *, key: str | None = None) -> None:
        del text, key
        raise self._unexpected()

    def now(self) -> datetime:
        raise self._unexpected()

    @staticmethod
    def _unexpected() -> RuntimeError:
        return RuntimeError("an extension must not use the hub before `start`")
