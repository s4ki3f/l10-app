"""Postgres itself rejects invalid data ("database constraints reject invalid data, not only the app").

Each test writes straight SQL, bypassing the app, and expects the database to refuse it.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.load import load_dataset
from app.db.seed import load_seed, reset_to_seed
from app.rules.report import build_report
from app.rules.weeks import parse_utc
from sqlalchemy.orm import Session

from tests.conftest import SEED

KEY = json.loads((Path(__file__).parents[1] / "rules" / "answer_key.json").read_text())
NOW = parse_utc("2026-11-11T15:00:00Z")


def rejects(db, sql: str, **params) -> None:
    sp = db.begin_nested()
    with pytest.raises(IntegrityError):
        db.execute(text(sql), params)
    sp.rollback()


def accepts(db, sql: str, **params) -> None:
    db.execute(text(sql), params)


ISSUE = "INSERT INTO issues (id, team_id, title, owner_id, created_at{cols}) VALUES ('ix', 'team_lead', 'x', 'u2', now(){vals})"


def issue_sql(**extra):
    cols = "".join(f", {k}" for k in extra)
    vals = "".join(f", {v}" for v in extra.values())
    return ISSUE.format(cols=cols, vals=vals)


class TestIssues:
    def test_priority_out_of_range(self, db):
        rejects(db, issue_sql(priority="6"))
        rejects(db, issue_sql(priority="0"))

    def test_blank_title(self, db):
        rejects(db, "INSERT INTO issues (id, team_id, title, owner_id, created_at) VALUES ('ix','team_lead','   ','u2',now())")

    def test_solved_without_note(self, db):
        rejects(db, issue_sql(solved_at="now()"))
        rejects(db, issue_sql(solved_at="now()", resolution_note="'  '"))

    def test_note_without_solved(self, db):
        rejects(db, issue_sql(resolution_note="'done'"))

    def test_source_must_be_both_or_neither(self, db):
        rejects(db, issue_sql(source_measurable_id="'m1'"))
        rejects(db, issue_sql(source_week="'2026-11-02'"))

    def test_source_week_must_be_monday(self, db):
        rejects(db, issue_sql(source_measurable_id="'m1'", source_week="'2026-10-27'"))

    def test_second_open_issue_for_same_cell(self, db):
        # i5 is already the unsolved Issue for m1 / week of Nov 2
        rejects(db, issue_sql(source_measurable_id="'m1'", source_week="'2026-11-02'"))

    def test_solved_issue_does_not_block_a_new_one(self, db):
        # i6 (m4 / Oct 26) is solved, so the cell can raise a fresh Issue
        accepts(db, issue_sql(source_measurable_id="'m4'", source_week="'2026-10-26'"))

    def test_owner_must_be_a_real_user(self, db):
        # who may own in a team (Admins anywhere, Team Members in their teams) is the API guard's job
        rejects(db, "INSERT INTO issues (id, team_id, title, owner_id, created_at) VALUES ('ix','team_lead','x','u999',now())")


class TestScoresAndGoals:
    def test_one_score_per_cell(self, db):
        rejects(db, "INSERT INTO scores (measurable_id, week_start, value) VALUES ('m1','2026-11-02',1)")

    def test_score_week_must_be_monday(self, db):
        rejects(db, "INSERT INTO scores (measurable_id, week_start, value) VALUES ('m1','2026-11-10',1)")

    def test_between_goal_needs_min_le_max(self, db):
        rejects(db, "UPDATE measurables SET goal_min = 5, goal_max = 4 WHERE id = 'm3'")

    def test_single_value_goal_cannot_have_a_range(self, db):
        rejects(db, "UPDATE measurables SET goal_min = 1 WHERE id = 'm1'")


class TestMeetings:
    def test_one_active_meeting_per_team(self, db):
        insert = ("INSERT INTO meetings (id, team_id, facilitator_id, started_at, section_started_at) "
                  "VALUES (:id, 'team_lead', 'u2', now(), now())")
        accepts(db, insert, id="live1")
        rejects(db, insert, id="live2")

    def test_rating_out_of_range(self, db):
        rejects(db, "UPDATE meeting_ratings SET rating = 11 WHERE meeting_id = 'mt1' AND user_id = 'u1'")

    def test_only_attendees_can_rate(self, db):
        # u6 did not attend mt1
        rejects(db, "INSERT INTO meeting_ratings (meeting_id, user_id, rating) VALUES ('mt1','u6',7)")

    def test_one_rating_per_person(self, db):
        rejects(db, "INSERT INTO meeting_ratings (meeting_id, user_id, rating) VALUES ('mt1','u1',7)")

    def test_section_must_be_known(self, db):
        rejects(db, "UPDATE meetings SET section = 'lunch' WHERE id = 'mt1'")


class TestUsers:
    def test_role_must_be_known(self, db):
        rejects(db, "UPDATE users SET role = 'owner' WHERE id = 'u1'")


def test_rules_report_from_database_matches_answer_key(db):
    report = build_report(load_dataset(Session(bind=db)), NOW)
    assert report == {k: v for k, v in KEY.items() if not k.startswith("_")}


def test_seed_is_safe_to_rerun(db):
    before = db.execute(text("SELECT count(*) FROM scores")).scalar()
    db.execute(text("UPDATE scores SET value = 999 WHERE measurable_id = 'm1' AND week_start = '2026-11-02'"))
    load_seed(db, SEED)
    load_seed(db, SEED)
    assert db.execute(text("SELECT count(*) FROM scores")).scalar() == before
    assert db.execute(text("SELECT value FROM scores WHERE measurable_id = 'm1' AND week_start = '2026-11-02'")).scalar() == 8


def test_reset_restores_exactly_the_seed(db):
    def snapshot():
        return {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                for t in ("issues", "todos", "scores", "meetings", "meeting_attendees", "meeting_ratings")}

    before = snapshot()
    db.execute(text("INSERT INTO issues (id, team_id, title, owner_id, created_at) VALUES ('i_new','team_lead','x','u2',now())"))
    db.execute(text("INSERT INTO scores (measurable_id, week_start, value) VALUES ('m1','2026-11-09',3)"))
    db.execute(text("UPDATE issues SET solved_at = now(), resolution_note = 'n' WHERE id = 'i1'"))
    db.execute(text("INSERT INTO meetings (id, team_id, facilitator_id, started_at, section_started_at) VALUES ('mt_new','team_lead','u2',now(),now())"))
    db.execute(text("INSERT INTO meeting_attendees (meeting_id, user_id) VALUES ('mt_new','u2'), ('mt1','u6')"))
    db.execute(text("UPDATE issues SET priority = 1, version = version + 3 WHERE id = 'i3'"))
    db.execute(text("UPDATE scores SET value = 2.5, version = version + 2 WHERE measurable_id = 'm2' AND week_start = '2026-11-02'"))
    reset_to_seed(db, SEED)
    assert snapshot() == before
    assert db.execute(text("SELECT solved_at FROM issues WHERE id = 'i1'")).scalar() is None
    # exactly the seed: versions too, so a client loading the fresh seed sees version 1 everywhere
    assert db.execute(text("SELECT max(version) FROM issues")).scalar() == 1
    assert db.execute(text("SELECT max(version) FROM scores")).scalar() == 1


def test_seed_rerun_survives_a_reraised_seed_issue(db):
    """Solve seeded i5, Make it an Issue again on its cell (m1 / Nov 2), then rerun the seed (compose does on
    every `up`). Reopening i5 would make two unsolved Issues for one cell; the rerun must not crash."""
    db.execute(text("UPDATE issues SET solved_at = now(), resolution_note = 'done' WHERE id = 'i5'"))
    db.execute(text("INSERT INTO issues (id, team_id, title, owner_id, created_at, source_measurable_id, source_week) "
                    "VALUES ('i_again','team_lead','New deals closed, week of Nov 2','u2',now(),'m1','2026-11-02')"))
    load_seed(db, SEED)
    open_for_cell = db.execute(text("SELECT count(*) FROM issues WHERE source_measurable_id='m1' "
                                    "AND source_week='2026-11-02' AND solved_at IS NULL")).scalar()
    assert open_for_cell == 1


class TestSameTeamAndTimeOrder:
    def test_todo_cannot_link_another_teams_issue(self, db):
        rejects(db, "INSERT INTO todos (id, team_id, title, owner_id, due_date, created_at, source_issue_id) "
                    "VALUES ('tx','team_ops','x','u8','2026-11-20',now(),'i1')")  # i1 is Leadership's

    def test_issue_cannot_come_from_another_teams_measurable(self, db):
        rejects(db, issue_sql(source_measurable_id="'m7'", source_week="'2026-11-02'"))  # m7 is Ops'

    def test_nothing_is_solved_or_done_before_it_was_created(self, db):
        rejects(db, "UPDATE issues SET solved_at = created_at - interval '1 minute', resolution_note = 'n' WHERE id = 'i1'")
        rejects(db, "UPDATE todos SET done_at = created_at - interval '1 minute' WHERE id = 't12'")


def test_clock_resumes_after_the_last_app_write(db):
    from datetime import timedelta

    from sqlalchemy.orm import Session as S

    from app.db.load import last_app_write

    assert last_app_write(S(bind=db)) is None  # fresh seed: the app starts exactly at APP_NOW
    db.execute(text("UPDATE issues SET priority = 4, updated_at = '2026-11-11T15:40:00Z' WHERE id = 'i3'"))
    assert last_app_write(S(bind=db)).isoformat() == "2026-11-11T15:40:00+00:00"


def test_reset_survives_an_issue_made_from_an_app_todo(db):
    """Issues and To-Dos point at each other now (To-Do from an Issue, Issue from a To-Do): reset must untangle it."""
    db.execute(text("INSERT INTO issues (id, team_id, title, owner_id, created_at) VALUES ('i_a','team_lead','A','u2','2026-11-01T15:00:00Z')"))
    db.execute(text("INSERT INTO todos (id, team_id, title, owner_id, due_date, created_at, source_issue_id) "
                    "VALUES ('t_t','team_lead','T','u2','2026-11-05','2026-11-01T15:00:00Z','i_a')"))
    db.execute(text("INSERT INTO issues (id, team_id, title, owner_id, created_at, source_todo_id) "
                    "VALUES ('i_b','team_lead','Overdue To-Do: T','u2','2026-11-10T15:00:00Z','t_t')"))
    reset_to_seed(db, SEED)
    assert db.execute(text("SELECT count(*) FROM issues WHERE id IN ('i_a','i_b')")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM todos WHERE id = 't_t'")).scalar() == 0
