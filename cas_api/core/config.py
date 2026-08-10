"""CAS API Configuration — environment'ten okur.

ÖNEMLİ: auth_secret -> AUTH_SECRET env değişkeni okuyor (mevcut cas_engine.py
ile UYUMLU). JWT_SECRET kullanmıyoruz çünkü mevcut sistem AUTH_SECRET kullanıyor.
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Service
    service_name: str = "cas-api"
    version: str = "0.1.0"
    environment: str = "production"

    # Database (DB_URL env'den)
    db_url: str = ""

    # JWT — mevcut cas_engine.py ile UYUMLU (AUTH_SECRET kullanıyor)
    auth_secret: str = ""  # AUTH_SECRET env'den
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24 * 7  # 1 hafta

    # CORS
    cors_origins: list[str] = [
        "https://www.casplatform.com",
        "https://casplatform.com",
    ]

    model_config = SettingsConfigDict(
        env_file="/opt/cas/.env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # AUTH_SECRET → auth_secret
        extra="ignore",
    )


settings = Settings()
