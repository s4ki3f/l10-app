"""Let the API's database role log in, with L10_APP_DB_PASSWORD (default "l10_app", for local use).

Compose's migrate step runs this after the migrations on every `docker compose up`, so the role's password always
matches the one the API is given: set or change L10_APP_DB_PASSWORD and the next `up` applies it. Migration 0009
creates the role and its grants; this only enables login. Where the password is managed elsewhere, don't run it.

    python -m app.db.app_role
"""
import os
import re
import sys

from sqlalchemy import text

from .session import get_engine

ROLE = "l10_app"


def password() -> str:
    value = os.environ.get("L10_APP_DB_PASSWORD") or "l10_app"  # empty counts as unset, as in compose's ${…:-…}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):  # it's part of the API's DATABASE_URL and a SQL literal
        raise SystemExit("L10_APP_DB_PASSWORD may only use letters, digits, '_' and '-'")
    return value


def main() -> int:
    with get_engine().begin() as conn:
        conn.execute(text(f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{password()}'"))
    print(f"{ROLE} can log in with L10_APP_DB_PASSWORD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
