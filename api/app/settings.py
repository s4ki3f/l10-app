"""Configuration from the environment."""
import os

# the API's least-privilege role (migration 0009); migrations and the seed tool are given the owner's URL
DEFAULT_DATABASE_URL = "postgresql+psycopg://l10_app:l10_app@localhost:5433/l10"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
