"""API test client: the real FastAPI app, a real Postgres session rolled back after each test, a frozen clock."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.main import app
from app.rules.clock import FrozenClock
from app.rules.weeks import parse_utc

NOW = parse_utc("2026-11-11T15:00:00Z")


@pytest.fixture
def session(db):
    # App commits become savepoints inside the test's outer transaction, which is rolled back. The app's
    # statements run as the API's own least-privilege role (migration 0009), as they do in production: a route
    # that needs a right the role lacks fails here first.
    db.execute(text("SET LOCAL ROLE l10_app"))
    with Session(bind=db, join_transaction_mode="create_savepoint", expire_on_commit=False) as s:
        yield s


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    original_clock = app.state.clock
    app.state.clock = FrozenClock(NOW)
    with TestClient(app) as c:
        yield c
    app.state.clock = original_clock
    app.dependency_overrides.clear()


@pytest.fixture
def as_user(client):
    """as_user('u2') signs the client in as that seeded user and returns it."""
    def sign_in(user_id: str) -> TestClient:
        client.cookies.clear()
        r = client.post("/auth/login", json={"user_id": user_id})
        assert r.status_code == 200, r.text
        return client
    return sign_in
