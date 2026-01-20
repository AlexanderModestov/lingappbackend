import json
from functools import lru_cache
from typing import List, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Environment toggle
    is_prod: bool = False

    # Production Supabase
    supabase_url: str
    supabase_key: str
    supabase_jwt_secret: str

    # Stage Supabase (used when is_prod=False)
    stage_supabase_url: str = ""
    stage_supabase_key: str = ""
    stage_supabase_jwt_secret: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""  # Pro monthly price ID from Stripe dashboard

    # Tier limits (configurable)
    free_uploads_per_week: int = 1
    free_quizzes_per_material: int = 3
    pro_uploads_per_week: int = 10
    pro_quizzes_per_material: int = 10
    pro_trial_days: int = 7

    # OpenAI
    openai_api_key: str

    # Application
    debug: bool = False
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    # API Settings
    api_v1_prefix: str = "/api/v1"

    def get_active_supabase_url(self) -> str:
        """Get the active Supabase URL based on environment."""
        if self.is_prod:
            return self.supabase_url
        return self.stage_supabase_url or self.supabase_url

    def get_active_supabase_key(self) -> str:
        """Get the active Supabase key based on environment."""
        if self.is_prod:
            return self.supabase_key
        return self.stage_supabase_key or self.supabase_key

    def get_active_supabase_jwt_secret(self) -> str:
        """Get the active Supabase JWT secret based on environment."""
        if self.is_prod:
            return self.supabase_jwt_secret
        return self.stage_supabase_jwt_secret or self.supabase_jwt_secret


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
