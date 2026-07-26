from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    discord_token: str
    database_path: str = "./cashualty.db"
    log_level: str = "INFO"
    dev_guild_id: int | None = None


settings = Settings()
