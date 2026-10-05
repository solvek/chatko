"""The config of a Briar instance and of its endpoints."""

import pytest
from pydantic import ValidationError

from chatko_briar import BriarConfig, BriarGroup

GROUP = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


def test_a_config_takes_the_url_and_the_token() -> None:
    config = BriarConfig.model_validate({"api": "http://briar:7000/", "auth_token": "secret"})

    assert config.api == "http://briar:7000"
    assert config.auth_token.get_secret_value() == "secret"
    assert "secret" not in repr(config)


@pytest.mark.parametrize(
    "api", ["briar:7000", "ftp://briar", "http://", "http://u:p@briar", "http://briar?x=1"]
)
def test_a_config_rejects_what_is_not_an_http_url(api: str) -> None:
    with pytest.raises(ValidationError):
        BriarConfig.model_validate({"api": api, "auth_token": "secret"})


def test_a_config_rejects_a_blank_token() -> None:
    with pytest.raises(ValidationError):
        BriarConfig.model_validate({"api": "http://briar:7000", "auth_token": "  "})


def test_an_endpoint_is_a_group_id() -> None:
    assert BriarGroup(group=GROUP).group_id == bytes(range(32))


@pytest.mark.parametrize("group", ["", "<Briar group id>", GROUP[:-1], GROUP + "="])
def test_an_endpoint_rejects_what_is_not_a_group_id(group: str) -> None:
    with pytest.raises(ValidationError):
        BriarGroup(group=group)
