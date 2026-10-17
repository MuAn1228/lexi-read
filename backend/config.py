from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    data_dir: Path = PROJECT_ROOT / "backend" / "data"
    database_url: str = ""
    app_mode: Literal["local", "public"] = "local"
    ocr_provider: str = "paddle"
    ocr_cpu_threads: int = Field(default=4, ge=1, le=8)
    tesseract_cmd: str = ""
    llm_provider: str = "offline"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 30
    llm_json_mode: bool = True
    llm_max_tokens: int = Field(default=4096, gt=0)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 25_000_000
    public_llm_daily_limit: int = Field(default=200, gt=0)
    public_llm_user_daily_limit: int = Field(default=20, gt=0)
    public_ocr_daily_limit: int = Field(default=50, gt=0)
    public_ocr_user_daily_limit: int = Field(default=5, gt=0)
    public_signup_daily_limit: int = Field(default=200, gt=0)
    public_signup_hourly_limit: int = Field(default=10, gt=0)
    public_max_documents_per_user: int = Field(default=30, gt=0)

    @model_validator(mode="after")
    def validate_public_origins(self):
        origins = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        if self.app_mode == "public" and (not origins or any("*" in origin for origin in origins)):
            raise ValueError("Public mode requires explicit CORS_ORIGINS; empty origins and wildcards are forbidden")
        return self

    @property
    def storage_dir(self) -> Path:
        return (PROJECT_ROOT / self.data_dir).resolve()

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.storage_dir / 'lexiread.sqlite3'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
