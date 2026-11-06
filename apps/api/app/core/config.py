from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str
    cors_origins: str = "http://localhost:8080"
    jwt_secret_key: str = "LOCAL-ONLY-change-this-secret-before-deployment-0123456789"
    access_token_minutes: int = 15
    refresh_token_days: int = 14

    model_config = SettingsConfigDict(case_sensitive=False)

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.app_env.lower() not in {"development", "test"} and self.jwt_secret_key.startswith("LOCAL-ONLY"):
            raise ValueError("Set a strong JWT_SECRET_KEY outside development/test")
        if len(self.jwt_secret_key) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 characters")
        if self.access_token_minutes < 1 or self.refresh_token_days < 1:
            raise ValueError("Token lifetimes must be positive")
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
