"""Every documented write route, and how to exercise it so the write happens at LATER.

Shared by the coverage tests (clock resume, audit trail). A new write route makes those tests fail until it
gets an entry here — so it can't silently skip either guarantee.
"""
from datetime import timedelta

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


def write_routes() -> set[tuple[str, str]]:
    """Every documented write route that changes data (from OpenAPI)."""
    return {(m, p) for m, p in team_scoped_routes() if m != "GET"} - NOT_DATA_WRITES
