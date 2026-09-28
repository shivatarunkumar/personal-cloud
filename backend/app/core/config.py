"""Every setting the API reads, from the environment (make/cloud.sh load .env first).

.env.example documents each key; the names here are the same, lowercased.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# GCS resumable uploads accept chunks in multiples of 256 KiB
CHUNK_MULTIPLE = 256 * 1024


def origin_of(url: str) -> str | None:
    """http://Mycloud.com:3002/drive → http://mycloud.com:3002 (what a browser sends as Origin)."""
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.hostname:
        return None
    default = {"http": 80, "https": 443}.get(parts.scheme)
    port = f":{parts.port}" if parts.port and parts.port != default else ""
    return f"{parts.scheme}://{parts.hostname.lower()}{port}"


class Settings(BaseSettings):
    # .env is read too, so the API also works when started without make (IDE, tests)
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_name: str = "Personal Cloud"
    app_env: str = "local"
    log_level: str = "INFO"
    log_format: str = "text"
    sql_echo: bool = False

    api_host_port: int = 8002
    web_host_port: int = 3002
    web_base_url: str = "http://localhost:3002"
    extra_origins: str = ""

    database_url: str = "postgresql+asyncpg://cloud:change-me@localhost:5432/personal_cloud"
    db_pool_size: int = 5

    jwt_secret: str = ""
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 30
    allow_signup: bool = True

    gcp_project_id: str = ""
    gcs_bucket: str = ""
    gcs_prefix: str = ""

    upload_chunk_bytes: int = 8 * 1024 * 1024
    max_upload_bytes: int = 50 * 1024**3
    default_quota_bytes: int | None = None
    trash_retention_days: int = 30

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("default_quota_bytes", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("gcs_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        value = value.strip().strip("/")
        return f"{value}/" if value else ""

    @field_validator("upload_chunk_bytes")
    @classmethod
    def _chunk(cls, value: int) -> int:
        # round down to a multiple GCS accepts, never below one unit
        return max(CHUNK_MULTIPLE, value - value % CHUNK_MULTIPLE)

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def secure_cookies(self) -> bool:
        return self.web_base_url.startswith("https://")

    @property
    def allowed_origins(self) -> list[str]:
        """Origins that may open the app and upload to the bucket.

        WEB_BASE_URL plus localhost on the web port are always in; EXTRA_ORIGINS adds more
        (a LAN or Tailscale address). The same list drives the API's CORS and the bucket's.
        """
        candidates = [
            self.web_base_url,
            f"http://localhost:{self.web_host_port}",
            f"http://127.0.0.1:{self.web_host_port}",
            *self.extra_origins.split(","),
        ]
        seen: list[str] = []
        for candidate in candidates:
            origin = origin_of(candidate) if candidate.strip() else None
            if origin and origin not in seen:
                seen.append(origin)
        return seen

    def problems(self) -> list[str]:
        """What stops the API from starting, each with its fix."""
        found = []
        if not self.jwt_secret:
            found.append("JWT_SECRET is empty in .env → set it to the output of: openssl rand -hex 32")
        elif len(self.jwt_secret) < 32:
            found.append("JWT_SECRET is shorter than 32 characters → use: openssl rand -hex 32")
        if not self.gcs_bucket:
            found.append("GCS_BUCKET is empty in .env → set it to your bucket's name")
        if not self.gcp_project_id:
            found.append("GCP_PROJECT_ID is empty in .env → set it to the project that owns the bucket")
        return found


@lru_cache
def get_settings() -> Settings:
    return Settings()
