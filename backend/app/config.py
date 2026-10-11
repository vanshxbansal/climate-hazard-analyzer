from __future__ import annotations

from datetime import date
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str = ""  # empty -> in-memory mongomock (dev/tests only)
    mongodb_db: str = "climate_hazards"
    allowed_origins: str = "http://localhost:5173"
    trust_proxy: bool = False  # honour the last X-Forwarded-For hop (Render/Cloud Run)

    openmeteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    openmeteo_geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    upstream_timeout_s: float = 45.0
    upstream_max_per_minute: int = 20  # global circuit-breaker on outbound calls

    rate_limit_per_minute: int = 60  # cheap endpoints, per client IP
    heavy_rate_limit_per_minute: int = 12  # /analysis & export, per client IP

    analysis_ttl_days: int = 30
    min_year: int = 1950  # ERA5 reliable start for this app

    @property
    def max_year(self) -> int:
        # only complete calendar years: a partial current year would bias counts down
        return date.today().year - 1

    @property
    def origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
