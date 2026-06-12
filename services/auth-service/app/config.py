from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SERVICE_HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8090
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"
    SQLALCHEMY_ECHO: bool = False
    EMAIL_ENABLED: bool = True
    EMAIL_SMTP_HOST: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_SMTP_HOST", "SMTP_SERVER", "AUTH_SMTP_SERVER"),
    )
    EMAIL_SMTP_PORT: int = Field(
        default=587,
        validation_alias=AliasChoices("EMAIL_SMTP_PORT", "SMTP_PORT", "AUTH_SMTP_PORT"),
    )
    EMAIL_SMTP_USERNAME: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_SMTP_USERNAME", "SMTP_USERNAME", "AUTH_SMTP_USERNAME", "EMAIL_SENDER"),
    )
    EMAIL_SMTP_PASSWORD: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_SMTP_PASSWORD", "EMAIL_PASSWORD", "AUTH_EMAIL_PASSWORD"),
    )
    EMAIL_FROM_ADDRESS: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_FROM_ADDRESS", "EMAIL_SENDER", "EMAIL_SMTP_USERNAME"),
    )
    PLATFORM_NAME: str = "Shivex"
    FRONTEND_BASE_URL: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices(
            "FRONTEND_BASE_URL",
            "APP_BASE_URL",
            "PUBLIC_BASE_URL",
            "NEXT_PUBLIC_APP_URL",
            "NEXT_PUBLIC_SITE_URL",
        ),
    )
    DEVICE_SERVICE_BASE_URL: str = "http://localhost:8000"
    AUTH_ALLOWED_ORIGINS: str = ""
    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_DOMAIN: str | None = None
    REFRESH_COOKIE_PATH: str = "/backend/auth/api/v1/auth"
    REFRESH_COOKIE_SAMESITE: str = "lax"
    BOOTSTRAP_SUPER_ADMIN_ENABLED: bool = False
    BOOTSTRAP_SUPER_ADMIN_EMAIL: str = ""
    BOOTSTRAP_SUPER_ADMIN_PASSWORD: str = ""
    BOOTSTRAP_SUPER_ADMIN_FULL_NAME: str = ""
    LOCAL_BOOTSTRAP_ENABLED: bool = False
    LOCAL_BOOTSTRAP_TENANT_ID: str = "SH00000001"
    LOCAL_BOOTSTRAP_TENANT_NAME: str = "Shivex Demo Tenant"
    LOCAL_BOOTSTRAP_TENANT_SLUG: str = "shivex-demo"
    LOCAL_BOOTSTRAP_PLANT_ID: str = "demo-plant"
    LOCAL_BOOTSTRAP_PLANT_NAME: str = "Demo Plant"
    LOCAL_BOOTSTRAP_PLANT_LOCATION: str = "Local Demo Plant"
    LOCAL_BOOTSTRAP_PLANT_TIMEZONE: str = "Asia/Kolkata"
    LOCAL_BOOTSTRAP_PREMIUM_FEATURES: str = "reports,waste_analysis,copilot,analytics"
    INVITE_TOKEN_EXPIRE_MINUTES: int = 30
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    ACTION_TOKEN_RETENTION_HOURS: int = Field(default=168, ge=1)
    PLATFORM_MAINTENANCE_DELIVERY_SYNC_INTERVAL_SECONDS: int = Field(default=60, ge=5)
    PLATFORM_MAINTENANCE_DELIVERY_BATCH_SIZE: int = Field(default=50, ge=1)
    PLATFORM_MAINTENANCE_EMAIL_MAX_RETRIES: int = Field(default=5, ge=1)
    PLATFORM_MAINTENANCE_EMAIL_BACKOFF_BASE_SECONDS: int = Field(default=60, ge=5)
    PLATFORM_MAINTENANCE_EMAIL_BACKOFF_MAX_SECONDS: int = Field(default=3600, ge=30)
    PLATFORM_MAINTENANCE_EMAIL_PROCESSING_TIMEOUT_SECONDS: int = Field(default=900, ge=30)
    LOGIN_RATE_LIMIT: str = "10/minute"
    PASSWORD_FORGOT_RATE_LIMIT: str = "5/minute"
    INVITATION_ACCEPT_RATE_LIMIT: str = "5/minute"

    @model_validator(mode="after")
    def _normalize_email_settings(self):
        if not self.EMAIL_FROM_ADDRESS:
            self.EMAIL_FROM_ADDRESS = self.EMAIL_SMTP_USERNAME
        if not self.EMAIL_SMTP_USERNAME:
            self.EMAIL_SMTP_USERNAME = self.EMAIL_FROM_ADDRESS
        self.EMAIL_SMTP_PASSWORD = self.EMAIL_SMTP_PASSWORD.replace(" ", "")
        return self

    @property
    def refresh_cookie_secure(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    @property
    def SMTP_SERVER(self) -> str:
        return self.EMAIL_SMTP_HOST

    @property
    def SMTP_PORT(self) -> int:
        return self.EMAIL_SMTP_PORT

    @property
    def SMTP_USERNAME(self) -> str:
        return self.EMAIL_SMTP_USERNAME

    @property
    def EMAIL_PASSWORD(self) -> str:
        return self.EMAIL_SMTP_PASSWORD

    @property
    def EMAIL_SENDER(self) -> str:
        return self.EMAIL_FROM_ADDRESS

    @property
    def local_bootstrap_premium_features(self) -> list[str]:
        return [
            feature.strip()
            for feature in self.LOCAL_BOOTSTRAP_PREMIUM_FEATURES.split(",")
            if feature.strip()
        ]


settings = Settings()
