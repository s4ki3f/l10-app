"""Fixture strategy.

- One test database, rebuilt at the start of every run: drop schema → `alembic upgrade head` → load seed.json.
  It refuses to run unless the database name ends in `_test`, so a real database can never be wiped.
- `db` gives each test a connection inside a transaction that is rolled back, so tests can't affect each other.
- Tests that need real concurrency (separate connections that really commit) use `committed_engine`
  and clean up after themselves.
- The clock is frozen at 2026-11-11T15:00:00Z (the seed's "now"); see tests/rules for other dates.
"""
import json
import os
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.seed import load_seed

API_ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((API_ROOT.parent / "seed.json").read_text(encoding="utf-8"))
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://l10:l10@localhost:5433/l10_test")


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}):
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    url = make_url(TEST_DATABASE_URL)
    if not url.database or not url.database.endswith("_test"):
        pytest.exit(f"refusing to run against {url.database!r}: test database name must end in _test", returncode=2)
    _ensure_database(TEST_DATABASE_URL)
    eng = create_engine(TEST_DATABASE_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(API_ROOT / "alembic.ini"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # migrations/env.py reads it
    command.upgrade(cfg, "head")
    with eng.begin() as conn:
        load_seed(conn, SEED)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """A connection whose work is rolled back after the test."""
    with engine.connect() as conn:
        tx = conn.begin()
        yield conn
        tx.rollback()


@pytest.fixture
def committed_engine(engine):
    """For tests that need real, separately committed connections. Clean up what you create."""
    return engine


def wait_until_blocked_by(engine, blocker_pid: int, timeout: float = 5.0) -> None:
    """Block until some backend is waiting on a lock held by `blocker_pid` (asked of Postgres, not a sleep).

    Race tests use it to prove the request under test really reached its write and is queued behind the
    competing transaction — so the interleaving they claim to test is the one that happened. Fails loudly if not.
    """
    deadline = time.monotonic() + timeout
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            waiting = probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"), {"pid": blocker_pid}).scalar()
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError(f"no request ever waited on transaction {blocker_pid}: the race was not exercised")
