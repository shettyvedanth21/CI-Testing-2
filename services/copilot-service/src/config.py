import logging
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="copilot-service")
    app_version: str = Field(default="1.0.0")
    log_level: str = Field(default="INFO")

    ai_provider: str = Field(default="groq")
    groq_api_key: str | None = Field(default=None)
    groq_model: str = Field(default="llama-3.3-70b-versatile")
    gemini_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)

    mysql_url: str | None = Field(default=None)
    mysql_readonly_url: str | None = Field(default=None)
    redis_url: str | None = Field(default=None)
    data_service_url: str | None = Field(default=None)
    reporting_service_url: str | None = Field(default=None)
    energy_service_url: str | None = Field(default=None)
    factory_timezone: str = Field(default="Asia/Kolkata")

    max_query_rows: int = Field(default=200)
    query_timeout_sec: int = Field(default=10)
    max_history_turns: int = Field(default=5)

    stage1_max_tokens: int = Field(default=500)
    stage2_max_tokens: int = Field(default=900)
    copilot_chat_rate_limit: str = Field(default="20/minute")


settings = Settings()

for _name in (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "MYSQL_URL",
    "MYSQL_READONLY_URL",
    "REDIS_URL",
    "DATA_SERVICE_URL",
    "REPORTING_SERVICE_URL",
    "ENERGY_SERVICE_URL",
):
    if getattr(settings, _name.lower()) is None:
        logger.warning("Missing environment variable for copilot-service setting: %s", _name)
