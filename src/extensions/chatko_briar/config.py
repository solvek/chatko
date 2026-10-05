"""The config of a Briar instance and of its endpoints (config.example.yaml)."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from chatko_briar import ids


class BriarConfig(BaseModel):
    """`api` is the URL of `briar-headless`; `auth_token` its token, through an environment
    variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api: str
    auth_token: SecretStr

    @field_validator("api")
    @classmethod
    def _is_a_url(cls, api: str) -> str:
        parts = urlsplit(api)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("not an http(s) URL like http://briar:7000")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("the URL has no credentials, query or fragment")
        return api.rstrip("/")

    @field_validator("auth_token")
    @classmethod
    def _is_not_blank(cls, token: SecretStr) -> SecretStr:
        if not token.get_secret_value().strip():
            raise ValueError("must not be blank")
        return token


class BriarGroup(BaseModel):
    """An endpoint: a private group, by the id that `briarctl` prints (43 characters of
    `A-Z a-z 0-9 - _`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str

    @field_validator("group")
    @classmethod
    def _is_a_group_id(cls, group: str) -> str:
        ids.from_text(group)
        return group

    @property
    def group_id(self) -> bytes:
        return ids.from_text(self.group)
