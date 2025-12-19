"""Application configuration via Pydantic BaseSettings."""

from pydantic import BaseSettings


class Settings(BaseSettings):
    """Typed settings for environment-driven configuration."""

    twilio_auth_token: str | None = None
    chatwoot_url: str | None = None
    openai_api_key: str | None = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
