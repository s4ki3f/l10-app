"""Postgres itself rejects invalid data ("database constraints reject invalid data, not only the app").

Each test writes straight SQL, bypassing the app, and expects the database to refuse it.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.load import load_dataset
from app.rules.report import build_report
from app.rules.weeks import parse_utc
from sqlalchemy.orm import Session

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

    def test_owner_must_belong_to_the_team(self, db):
        rejects(db, "INSERT INTO issues (id, team_id, title, owner_id, created_at) VALUES ('ix','team_lead','x','u6',now())")


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
    from tests.conftest import SEED
    from app.db.seed import load_seed

    before = db.execute(text("SELECT count(*) FROM scores")).scalar()
    db.execute(text("UPDATE scores SET value = 999 WHERE measurable_id = 'm1' AND week_start = '2026-11-02'"))
    load_seed(db, SEED)
    load_seed(db, SEED)
    assert db.execute(text("SELECT count(*) FROM scores")).scalar() == before
    assert db.execute(text("SELECT value FROM scores WHERE measurable_id = 'm1' AND week_start = '2026-11-02'")).scalar() == 8


def test_reset_restores_exactly_the_seed(db):
    from tests.conftest import SEED
    from app.db.seed import reset_to_seed

    def snapshot():
        return {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                for t in ("issues", "todos", "scores", "meetings", "meeting_attendees", "meeting_ratings")}

    before = snapshot()
    db.execute(text("INSERT INTO issues (id, team_id, title, owner_id, created_at) VALUES ('i_new','team_lead','x','u2',now())"))
    db.execute(text("INSERT INTO scores (measurable_id, week_start, value) VALUES ('m1','2026-11-09',3)"))
    db.execute(text("UPDATE issues SET solved_at = now(), resolution_note = 'n' WHERE id = 'i1'"))
    db.execute(text("INSERT INTO meetings (id, team_id, facilitator_id, started_at, section_started_at) VALUES ('mt_new','team_lead','u2',now(),now())"))
    db.execute(text("INSERT INTO meeting_attendees (meeting_id, user_id) VALUES ('mt_new','u2'), ('mt1','u6')"))
    reset_to_seed(db, SEED)
    assert snapshot() == before
    assert db.execute(text("SELECT solved_at FROM issues WHERE id = 'i1'")).scalar() is None
