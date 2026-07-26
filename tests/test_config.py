from __future__ import annotations

import pytest

from cashualty.config import Settings


def test_empty_dev_guild_id_env_var_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: docker-compose's `env_file:` directive injects
    DEV_GUILD_ID as a real (empty-string) environment variable rather than
    leaving it unset, which used to fail pydantic validation for the
    `int | None` field. `_env_file=None` skips this repo's local .env so the
    test only sees the environment variables set below."""
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DEV_GUILD_ID", "")

    settings = Settings(_env_file=None)

    assert settings.dev_guild_id is None
