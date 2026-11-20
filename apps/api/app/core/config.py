from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str
    cors_origins: str = "http://localhost:8080"
    jwt_secret_key: SecretStr
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    temporary_password_hours: int = 24
    expose_temporary_passwords: bool = False
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_from: str = "nexus@localhost"
    app_login_url: str = "http://localhost:8080/login"

    model_config = SettingsConfigDict(case_sensitive=False)

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        secret = self.jwt_secret_key.get_secret_value()
        normalized = secret.strip().casefold()
        known_placeholders = {
            "secret", "changeme", "change-me", "password", "your-secret-key",
            "your_jwt_secret_key", "replace_me", "example", "test-secret",
            "local-only-change-this-secret-before-deployment-0123456789",
        }
        placeholder_markers = ("local-only", "change-me", "changeme", "replace", "placeholder", "your-secret", "example-secret")
        if normalized in known_placeholders or any(marker in normalized for marker in placeholder_markers):
            raise ValueError("JWT_SECRET_KEY must not use a placeholder or known default")
        if self.expose_temporary_passwords and self.app_env.lower() != "development":
            raise ValueError("Temporary password responses are only allowed in development")
        if len(secret) < 32 or len(set(secret)) < 8:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 characters")
        if self.access_token_minutes < 1 or self.refresh_token_days < 1 or self.temporary_password_hours < 1:
            raise ValueError("Token and temporary password lifetimes must be positive")
        return self

    @property
    def jwt_signing_key(self) -> str:
        return self.jwt_secret_key.get_secret_value()

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
