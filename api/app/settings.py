"""Configuration from the environment."""
import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://l10:l10@localhost:5433/l10"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
