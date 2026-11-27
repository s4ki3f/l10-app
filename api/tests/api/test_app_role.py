"""Every route works under the API's least-privilege database role (the stack runs the API as it: migration 0009)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.main import app
from app.rules.clock import FrozenClock
from tests.db.test_app_role import app_url

from .conftest import NOW
from .test_authorization import fill, team_scoped_routes
from .writes import WRITES


@pytest.fixture(scope="module")
def app_engine(engine):
    eng = create_engine(app_url())
    yield eng
    eng.dispose()


@pytest.fixture
def as_app_user(app_engine):
    """Like `as_user`, but every request's session connects as l10_app (rolled back after the test)."""
    with app_engine.connect() as conn:
        tx = conn.begin()
        with Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False) as s:
            app.dependency_overrides[get_db] = lambda: s
            original_clock = app.state.clock
            app.state.clock = FrozenClock(NOW)
            with TestClient(app) as c:
                def sign_in(user_id: str) -> TestClient:
                    c.cookies.clear()
                    assert c.post("/auth/login", json={"user_id": user_id}).status_code == 200
                    return c
                yield sign_in
            app.state.clock = original_clock
            app.dependency_overrides.clear()
        tx.rollback()


@pytest.mark.parametrize("route", sorted(WRITES))
def test_every_write_route_works_under_the_app_role(as_app_user, route):
    WRITES[route](as_app_user("u3"))  # act() asserts the write succeeded


@pytest.mark.parametrize("path", sorted(p for m, p in set(team_scoped_routes()) if m == "GET" and not p.endswith("/events")))
def test_every_read_route_works_under_the_app_role(as_app_user, path):
    assert as_app_user("u2").get(fill(path)).status_code == 200
