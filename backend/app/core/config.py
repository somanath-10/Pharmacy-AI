"""Application configuration. All values overridable via environment (.env)."""
from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Pharma AI OS"
    ENV: str = "development"  # development | staging | production
    API_PREFIX: str = "/api"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Database
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "pharmacy_ai_os"
    USE_TXNS: bool = True

    # Redis (optional)
    REDIS_URL: Optional[str] = ""

    # Auth
    JWT_SECRET: str = "dev-secret-change-me-must-be-at-least-32-bytes-long"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15   # short-lived access tokens
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    REFRESH_ROTATION_GRACE_SECONDS: int = 30  # reuse-detection grace window

    # Account security
    MAX_LOGIN_FAILURES: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15
    RATE_LIMIT_LOGIN_PER_MIN: int = 10
    RATE_LIMIT_API_PER_MIN: int = 600
    PASSWORD_MIN_LENGTH: int = 10
    PASSWORD_REQUIRE_CLASSES: int = 3  # upper/lower/digit/symbol

    # MFA (TOTP; MFA-ready architecture)
    MFA_ENABLED: bool = False           # global kill-switch (tests/dev)
    MFA_ENFORCE_ROLES: str = "SUPER_ADMIN"  # roles that MUST enroll

    # AI
    OPENAI_API_KEY: Optional[str] = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    AI_TIMEOUT_SECONDS: int = 30

    # Storage: s3 | gridfs | local
    STORAGE_BACKEND: str = "auto"
    S3_ENDPOINT: Optional[str] = ""
    S3_BUCKET: Optional[str] = "pharmaos-documents"
    AWS_ACCESS_KEY_ID: Optional[str] = ""
    AWS_SECRET_ACCESS_KEY: Optional[str] = ""
    LOCAL_UPLOAD_DIR: str = "uploads"

    # Features
    ENABLE_MARKET_TO_ORDER: bool = True
    ENABLE_AGENTS: bool = True
    SEED_DEMO: bool = False

    # Notifications
    EMAIL_MODE: str = "SIMULATED"  # V1: simulated only

    # Idempotency
    IDEMPOTENCY_TTL_HOURS: int = 48

    # Limits / policies defaults
    PO_AUTO_APPROVAL_LIMIT: float = 100000.0
    PAYMENT_AUTHORIZATION_LIMIT: float = 250000.0
    MATCH_TOLERANCE_PCT: float = 1.0
    RATE_LIMIT_PER_MIN: int = 600

    # Planning autonomy: routine proposals auto-execute below this value;
    # anything above (or an anomaly) escalates to humans.
    PLANNING_AUTO_EXECUTION_LIMIT: float = 200000.0
    DEMAND_ANOMALY_FACTOR: float = 3.0  # demand > factor*forecast = anomaly

    # Vendor policy
    LICENCE_EXPIRY_BLOCK_DAYS: int = 0  # block when licence expired N days ago
    LICENCE_EXPIRY_WARN_DAYS: int = 60

    # WMS / quality phase
    EQUIPMENT_DUE_WARN_DAYS: int = 30          # warn N days before calibration/maintenance due
    INVENTORY_MIN_SHELF_LIFE_DAYS: int = 0     # FEFO min remaining shelf life (0 = off)
    EXPIRY_RISK_WARN_DAYS: int = 90            # expiry-risk detection horizon
    PUTAWAY_PICKING_FREQUENCY_BOOST: bool = True  # putaway prefers fast-moving zones
    TRANSFER_APPROVAL_THRESHOLD: float = 0.0   # transfers above this value need approval (0 = never)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @model_validator(mode="after")
    def _enforce_production_secrets(self):
        """Refuse to boot staging/production with a weak or default JWT secret.

        RFC 7518 requires >= 32 bytes for HS256; the dev default should
        be replaced in production and staging environments.
        """
        weak = (not self.JWT_SECRET
                or self.JWT_SECRET.startswith("dev-secret-change-me")
                or len(self.JWT_SECRET.encode()) < 32)
        if weak and self.ENV in {"production", "staging"}:
            raise ValueError(
                "JWT_SECRET must be a long random value (>= 32 bytes) when "
                f"ENV={self.ENV}; refusing to boot with an insecure secret")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
