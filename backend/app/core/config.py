"""Application configuration. All values overridable via environment (.env)."""
from functools import lru_cache
from typing import Optional

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
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

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

    # Limits / policies defaults
    PO_AUTO_APPROVAL_LIMIT: float = 100000.0
    PAYMENT_AUTHORIZATION_LIMIT: float = 250000.0
    MATCH_TOLERANCE_PCT: float = 1.0
    RATE_LIMIT_PER_MIN: int = 600

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
