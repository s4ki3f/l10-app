"""The clock resumes after the latest app write (so a restart never runs time backwards).

Coverage, not examples: every documented write route is exercised at a later moment, and the resume point
must move to exactly that moment. A new write route without an entry here fails the suite.
"""
from datetime import timedelta

import pytest

from app.db.load import last_app_write
from app.main import app
from app.rules.clock import FrozenClock

from .conftest import NOW
from .test_authorization import team_scoped_routes

LATER = NOW + timedelta(hours=1)
NOT_DATA_WRITES = {("POST", "/auth/login"), ("POST", "/auth/logout")}


def at(moment):
    app.state.clock = FrozenClock(moment)


def live_meeting(c, section="scorecard"):
    """Start a Leadership meeting as u3 at NOW, optionally advanced; return its id."""
    at(NOW)
    mt = c.post("/teams/team_lead/meetings").json()["id"]
    order = ["scorecard", "ids", "conclude"]
    for s in order[: order.index(section)]:
        c.post(f"/meetings/{mt}/advance", json={"from_section": s})
    return mt


def act(c, method, path, json=None):
    at(LATER)
    r = c.request(method, path, json=json)
    assert r.status_code in (200, 201), r.text


# (method, path) → how to perform it, as u3, ending with the write happening at LATER
WRITES = {
    ("POST", "/teams/{team_id}/issues"): lambda c: act(c, "POST", "/teams/team_lead/issues", {"title": "x"}),
    ("POST", "/issues/{issue_id}/todos"): lambda c: act(c, "POST", "/issues/i1/todos", {"title": "x", "owner_id": "u3"}),
    ("PATCH", "/issues/{issue_id}"): lambda c: act(c, "PATCH", "/issues/i3", {"priority": 1}),
    ("POST", "/issues/{issue_id}/solve"): lambda c: act(c, "POST", "/issues/i3/solve", {"note": "done"}),
    ("POST", "/todos/{todo_id}/done"): lambda c: act(c, "POST", "/todos/t31/done"),
    ("POST", "/todos/{todo_id}/undo"): lambda c: act(c, "POST", "/todos/t21/undo"),
    ("PUT", "/measurables/{measurable_id}/weeks/{week}/score"): lambda c: act(c, "PUT", "/measurables/m1/weeks/2026-11-09/score", {"value": 3}),
    ("POST", "/measurables/{measurable_id}/weeks/{week}/issue"): lambda c: act(c, "POST", "/measurables/m1/weeks/2026-10-26/issue"),
    ("POST", "/teams/{team_id}/meetings"): lambda c: act(c, "POST", "/teams/team_lead/meetings"),
    ("POST", "/rocks/{rock_id}/issue"): lambda c: act(c, "POST", "/rocks/r2/issue"),  # r2 is off track
    ("POST", "/todos/{todo_id}/issue"): lambda c: act(c, "POST", "/todos/t31/issue"),  # t31 is overdue
    ("POST", "/meetings/{meeting_id}/advance"): lambda c: act(c, "POST", f"/meetings/{live_meeting(c)}/advance", {"from_section": "scorecard"}),
    ("POST", "/meetings/{meeting_id}/end"): lambda c: act(c, "POST", f"/meetings/{live_meeting(c)}/end"),
    ("PUT", "/meetings/{meeting_id}/rating"): lambda c: act(c, "PUT", f"/meetings/{live_meeting(c, 'conclude')}/rating", {"rating": 9}),
    ("POST", "/meetings/{meeting_id}/join"): lambda c: (live_meeting(c), c.post("/auth/login", json={"user_id": "u2"}),
                                                         act(c, "POST", f"/meetings/{_live_id(c)}/join")),
}


def _live_id(c):
    return c.get("/teams/team_lead/meetings/active").json()["meeting"]["id"]


def test_every_write_route_is_covered():
    writes = {(m, p) for m, p in team_scoped_routes() if m != "GET"} - NOT_DATA_WRITES
    assert writes <= set(WRITES), f"add a clock-resume case for: {sorted(writes - set(WRITES))}"


@pytest.mark.parametrize("route", sorted(WRITES))
def test_the_clock_resumes_after_this_write(as_user, session, route):
    c = as_user("u3")
    WRITES[route](c)
    session.expire_all()
    assert last_app_write(session) == LATER, f"{route} wrote at {LATER} but the clock would resume at {last_app_write(session)}"
