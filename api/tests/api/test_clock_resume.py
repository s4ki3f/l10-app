"""The clock resumes after the latest app write (so a restart never runs time backwards).

Coverage, not examples: every documented write route is exercised at a later moment, and the resume point
must move to exactly that moment. A new write route without an entry in writes.py fails the suite.
"""
import pytest

from app.db.load import last_app_write

from .writes import LATER, WRITES, write_routes


def test_every_write_route_is_covered():
    assert write_routes() <= set(WRITES), f"add a case in writes.py for: {sorted(write_routes() - set(WRITES))}"


@pytest.mark.parametrize("route", sorted(WRITES))
def test_the_clock_resumes_after_this_write(as_user, session, route):
    c = as_user("u3")
    WRITES[route](c)
    session.expire_all()
    assert last_app_write(session) == LATER, f"{route} wrote at {LATER} but the clock would resume at {last_app_write(session)}"
