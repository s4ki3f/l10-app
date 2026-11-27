"""Roles and team boundaries (brief 2.4, 2.7). Enforced on the server, proven here.

Seed: u1 admin (lead) · u2 member (lead, sales) · u3/u4 members · u5 observer (lead) ·
u6 member (sales) · u7 observer (sales) · u8/u9 members (ops).
"""
import re

import pytest
from sqlalchemy import func, select

from app.api.deps import SESSION_COOKIE, sign_session
from app.db import models as m
from app.main import app


def error_code(r):
    return r.json()["error"]["code"]


def issue_count(session):
    return session.scalar(select(func.count()).select_from(m.Issue))


# ---------- who you are ----------

class TestSignIn:
    def test_no_session_is_401(self, client):
        r = client.get("/teams/team_lead/issues")
        assert (r.status_code, error_code(r)) == (401, "not_signed_in")

    def test_tampered_cookie_is_401(self, client):
        token = sign_session("u6")
        forged = token.replace(token.split(".")[0], sign_session("u1").split(".")[0])  # u1's payload, u6's signature
        client.cookies.set(SESSION_COOKIE, forged)
        assert client.get("/me").status_code == 401

    def test_unknown_user_cannot_sign_in(self, client):
        assert client.post("/auth/login", json={"user_id": "u999"}).status_code == 401

    def test_me_lists_only_your_teams(self, as_user):
        assert {t["id"] for t in as_user("u2").get("/me").json()["teams"]} == {"team_lead", "team_sales"}

    def test_admin_sees_every_team(self, as_user):
        assert {t["id"] for t in as_user("u1").get("/me").json()["teams"]} == {"team_lead", "team_sales", "team_ops"}

    def test_identity_comes_from_the_cookie_not_the_body(self, as_user, session):
        c = as_user("u3")
        r = c.post("/teams/team_lead/issues", json={"title": "spoof", "owner_id": "u1"})
        assert r.status_code == 422  # unknown field rejected outright
        r = c.post("/teams/team_lead/issues", json={"title": "mine"})
        assert r.json()["owner"]["id"] == "u3"


# ---------- brief minimum: a Team Member reading another team's data is refused ----------

class TestTeamBoundaries:
    def test_member_reading_another_team_gets_404(self, as_user):
        r = as_user("u6").get("/teams/team_lead/issues")  # u6 is only in Sales
        assert (r.status_code, error_code(r)) == (404, "not_found")

    def test_other_team_looks_the_same_as_a_missing_team(self, as_user):
        c = as_user("u6")
        assert c.get("/teams/team_lead").json() == c.get("/teams/no_such_team").json()

    def test_guessed_record_id_in_another_team_is_404(self, as_user, session):
        before = session.scalar(select(func.count()).select_from(m.Todo))
        r = as_user("u6").post("/issues/i1/todos", json={"title": "x", "owner_id": "u6"})  # i1 belongs to Leadership
        assert r.status_code == 404
        assert session.scalar(select(func.count()).select_from(m.Todo)) == before

    def test_existing_record_in_another_team_is_indistinguishable_from_a_missing_one(self, as_user):
        c = as_user("u6")
        body = {"title": "x", "owner_id": "u6"}
        real, missing = c.post("/issues/i1/todos", json=body), c.post("/issues/i_nope/todos", json=body)
        assert (real.status_code, real.json()) == (missing.status_code, missing.json())

    def test_team_comes_from_the_record_not_the_url(self, as_user):
        # u6 can write in Sales, but i1 is a Leadership Issue: the record's team decides
        assert as_user("u6").post("/issues/i1/todos", json={"title": "x", "owner_id": "u6"}).status_code == 404

    def test_observer_cannot_read_a_team_they_are_not_in(self, as_user):
        assert as_user("u7").get("/teams/team_lead/issues").status_code == 404

    def test_admin_reads_and_writes_every_team(self, as_user):
        c = as_user("u1")  # u1 is only a member of Leadership
        assert c.get("/teams/team_ops/issues").status_code == 200
        assert c.post("/issues/i9/todos", json={"title": "chase carrier", "owner_id": "u8"}).status_code == 201


# ---------- brief minimum: an Observer write is refused ----------

class TestObserver:
    def test_observer_can_read_their_team(self, as_user):
        assert as_user("u5").get("/teams/team_lead/issues").status_code == 200

    def test_observer_write_is_403_and_nothing_changes(self, as_user, session):
        before = issue_count(session)
        r = as_user("u5").post("/teams/team_lead/issues", json={"title": "observer tries"})
        assert (r.status_code, error_code(r)) == (403, "forbidden")
        assert issue_count(session) == before

    def test_observer_cannot_add_a_todo(self, as_user):
        assert as_user("u5").post("/issues/i1/todos", json={"title": "x", "owner_id": "u2"}).status_code == 403


# ---------- owners ----------

class TestOwners:
    def test_todo_cannot_be_assigned_to_an_observer(self, as_user):
        r = as_user("u2").post("/issues/i1/todos", json={"title": "x", "owner_id": "u5"})
        assert (r.status_code, error_code(r)) == (422, "invalid_owner")

    def test_todo_cannot_be_assigned_outside_the_team(self, as_user):
        r = as_user("u2").post("/issues/i1/todos", json={"title": "x", "owner_id": "u6"})
        assert (r.status_code, error_code(r)) == (422, "invalid_owner")

    def test_admin_owns_in_every_team(self, as_user):
        # brief 2.4: an Admin can do everything a Team Member can, in every team (u1 is only in Leadership)
        c = as_user("u1")
        r = c.post("/teams/team_ops/issues", json={"title": "Admin raises it"})
        assert (r.status_code, r.json()["owner"]["id"]) == (201, "u1")
        assert c.post("/issues/i9/todos", json={"title": "x", "owner_id": "u1"}).status_code == 201

    def test_team_member_can_assign_an_admin(self, as_user):
        assert as_user("u8").post("/issues/i9/todos", json={"title": "x", "owner_id": "u1"}).status_code == 201

    def test_team_member_of_another_team_cannot_own_here(self, as_user):
        r = as_user("u8").post("/issues/i9/todos", json={"title": "x", "owner_id": "u6"})  # u6 is Sales only
        assert (r.status_code, error_code(r)) == (422, "invalid_owner")

    def test_todo_from_issue_is_due_in_7_days_and_linked(self, as_user):
        r = as_user("u2").post("/issues/i1/todos", json={"title": "Call recruiter", "owner_id": "u3"})
        body = r.json()
        assert (r.status_code, body["due_date"], body["source_issue"]["id"]) == (201, "2026-11-18", "i1")


# ---------- input validation at the API ----------

class TestValidation:
    @pytest.mark.parametrize("payload", [{}, {"title": ""}, {"title": "   "}, {"title": "x", "priority": 6},
                                         {"title": "x", "priority": 0}, {"title": "x" * 201}])
    def test_bad_issue_input_is_422(self, as_user, payload):
        r = as_user("u2").post("/teams/team_lead/issues", json=payload)
        assert (r.status_code, error_code(r)) == (422, "invalid_input")

    def test_every_error_has_one_shape(self, client, as_user):
        responses = [client.get("/me"), as_user("u6").get("/teams/team_lead/issues"),
                     as_user("u5").post("/teams/team_lead/issues", json={"title": "x"}),
                     as_user("u2").post("/teams/team_lead/issues", json={})]
        for r in responses:
            assert set(r.json()["error"]) >= {"code", "message"}


# ---------- the guard can't be forgotten: walk every route ----------

def team_scoped_routes():
    """Every documented route with a {team_id} or {…_id} parameter, read from the OpenAPI schema
    (the public contract), so nested routers can't hide a route from the walk."""
    for path, ops in app.openapi()["paths"].items():
        if re.search(r"\{(team_id|\w+_id)\}", path):
            for method in ops:
                yield method.upper(), path


def fill(path: str) -> str:
    # point every id at a Leadership record that Sales-only u6 must not reach
    return re.sub(r"\{(\w+)\}", lambda mt: {"team_id": "team_lead", "issue_id": "i1", "todo_id": "t30",
                                             "meeting_id": "mt1", "measurable_id": "m1", "week": "2026-11-02", "rock_id": "r2"}.get(mt.group(1), "x"), path)


# A minimal VALID body per write route, so validation can't answer first and hide a missing guard.
VALID_BODY = {
    ("POST", "/teams/{team_id}/issues"): {"title": "probe"},
    ("POST", "/issues/{issue_id}/todos"): {"title": "probe", "owner_id": "u2"},
    ("PATCH", "/issues/{issue_id}"): {"priority": 3},
    ("POST", "/issues/{issue_id}/solve"): {"note": "probe"},
    ("PUT", "/measurables/{measurable_id}/weeks/{week}/score"): {"value": 1},
    ("POST", "/meetings/{meeting_id}/advance"): {"from_section": "scorecard"},
    ("PUT", "/meetings/{meeting_id}/rating"): {"rating": 5},
}
OBSERVER_MAY = {("POST", "/meetings/{meeting_id}/join")}  # Observers can join meetings


@pytest.mark.parametrize("method,path", sorted(set(team_scoped_routes())))
def test_every_scoped_route_guards_anonymous_outsiders_and_observers(client, as_user, method, path):
    body = VALID_BODY.get((method, path))
    client.cookies.clear()
    assert client.request(method, fill(path), json=body).status_code == 401, "anonymous must be 401"
    r = as_user("u6").request(method, fill(path), json=body)  # u6: Sales only; every id points at Leadership
    assert (r.status_code, error_code(r)) == (404, "not_found"), f"outsider got {r.status_code}"
    if method != "GET" and (method, path) not in OBSERVER_MAY:
        r = as_user("u5").request(method, fill(path), json=body)  # u5: Observer in Leadership
        assert (r.status_code, error_code(r)) == (403, "forbidden"), f"observer got {r.status_code}"


def test_every_write_route_has_a_valid_probe_body():
    writes = {(m, p) for m, p in team_scoped_routes() if m != "GET"}
    bodiless = {("POST", "/teams/{team_id}/meetings"), ("POST", "/meetings/{meeting_id}/join"),
                ("POST", "/meetings/{meeting_id}/end"), ("POST", "/todos/{todo_id}/done"), ("POST", "/todos/{todo_id}/undo"),
                ("POST", "/measurables/{measurable_id}/weeks/{week}/issue"),
                ("POST", "/rocks/{rock_id}/issue"), ("POST", "/todos/{todo_id}/issue")}
    assert writes - bodiless <= set(VALID_BODY), f"add a probe body for: {sorted(writes - bodiless - set(VALID_BODY))}"


def test_route_walk_found_routes():
    assert len(set(team_scoped_routes())) >= 4


class TestMalformedInputIsNeverA500:
    """From the parallel security review: odd bytes and types must be a 4xx in the one error shape."""

    @pytest.mark.parametrize("path", ["/teams/%00", "/meetings/%00/summary", "/teams/team_lead%20x/issues", "/issues/" + "x" * 65 + "/solve"])
    def test_bad_ids_in_the_path_are_422(self, as_user, path):
        c = as_user("u1")
        r = c.post(path, json={"note": "x"}) if path.endswith("/solve") else c.get(path)
        assert (r.status_code, error_code(r)) == (422, "invalid_input")

    def test_nul_in_text_is_422(self, as_user):
        r = as_user("u2").post("/teams/team_lead/issues", json={"title": "bad\u0000title"})
        assert (r.status_code, error_code(r)) == (422, "invalid_input")

    def test_nul_in_login_is_422(self, client):
        assert client.post("/auth/login", json={"user_id": "u\u0000"}).status_code == 422

    def test_non_ascii_cookie_is_401(self, client):
        # raw bytes, as curl or a stray browser cookie sends them (httpx won't encode non-ASCII cookies itself)
        client.cookies.clear()
        raw = f"{SESSION_COOKIE}=x.".encode() + "é".encode("utf-8")
        assert client.get("/me", headers={"cookie": raw}).status_code == 401

    @pytest.mark.parametrize("body", [{"priority": True}, {"priority": "3"}, {"priority": 3.0}])
    def test_priority_must_be_a_real_integer(self, as_user, body):
        assert as_user("u2").patch("/issues/i3", json=body).status_code == 422


def test_sessions_expire(client):
    from app.api.deps import SESSION_MAX_AGE_SECONDS
    import time as _time

    client.cookies.set(SESSION_COOKIE, sign_session("u2", issued_at=int(_time.time()) - SESSION_MAX_AGE_SECONDS - 5))
    assert client.get("/me").status_code == 401
    client.cookies.set(SESSION_COOKIE, sign_session("u2"))
    assert client.get("/me").status_code == 200
