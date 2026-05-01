"""
Configuration - loads from environment variables
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Microsoft OAuth / Entra ID
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"  # or your specific tenant
    MICROSOFT_REDIRECT_URI: str = "http://localhost:8000/api/auth/callback"
    MICROSOFT_SCOPES: str = "openid profile email Mail.Read Mail.Send offline_access"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # App
    SECRET_KEY: str = "change-me-in-production-use-strong-random-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ENVIRONMENT: str = "development"

    # Microsoft Graph
    GRAPH_API_BASE: str = "https://graph.microsoft.com/v1.0"
    AUTHORITY_URL: str = "https://login.microsoftonline.com"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# OAuth scopes list
OAUTH_SCOPES = settings.MICROSOFT_SCOPES.split()
