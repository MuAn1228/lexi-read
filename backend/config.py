from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    data_dir: Path = PROJECT_ROOT / "backend" / "data"
    database_url: str = ""
    ocr_provider: str = "paddle"
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

    @property
    def storage_dir(self) -> Path:
        return (PROJECT_ROOT / self.data_dir).resolve()

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.storage_dir / 'lexiread.sqlite3'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
