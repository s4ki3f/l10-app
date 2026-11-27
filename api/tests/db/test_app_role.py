"""The API's own database role (migration 0009): least privilege, so even the app's credentials can't rewrite
history. These tests switch an owner connection to the role with SET ROLE (no password involved), rolled back."""
import importlib.util
import re

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from app.db import app_role
from app.db.load import last_app_write
from tests.conftest import API_ROOT

ROLE = "l10_app"
EVENT = ("INSERT INTO audit_events (team_id, actor_id, action, entity_type, entity_id, entity_label, at) "
         "VALUES ('team_lead', 'u2', 'issue.created', 'issue', 'role-test', 'x', now())")


@pytest.fixture
def as_app(db):
    db.execute(text(f"SET LOCAL ROLE {ROLE}"))  # ends with the test's transaction
    return db


def refused(conn, sql: str, why: str) -> None:
    sp = conn.begin_nested()
    with pytest.raises(ProgrammingError, match=why):
        conn.execute(text(sql))
    sp.rollback()


def test_the_role_may_write_exactly_what_the_app_writes(db):
    """The whole map of rights in one place: anything granted beyond it, or missing from it, fails here."""
    writes = dict(db.execute(text(
        "SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) FROM information_schema.role_table_grants "
        "WHERE grantee = :r AND privilege_type <> 'SELECT' GROUP BY table_name"), {"r": ROLE}).all())
    assert writes == {**{t: "INSERT, UPDATE" for t in ("issues", "todos", "scores", "meetings", "meeting_ratings")},
                      "meeting_attendees": "INSERT"}
    audit_insert = db.execute(text(
        "SELECT array_agg(column_name::text ORDER BY column_name) FROM information_schema.column_privileges "
        "WHERE grantee = :r AND table_name = 'audit_events' AND privilege_type = 'INSERT'"), {"r": ROLE}).scalar()
    assert audit_insert == sorted(["team_id", "actor_id", "action", "entity_type", "entity_id", "entity_label", "changes", "at"])
    unreadable = db.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                                 "AND NOT has_table_privilege(:r, format('%I.%I', schemaname, tablename), 'SELECT')"), {"r": ROLE}).scalar()
    assert unreadable == 0


def test_the_app_reads_and_writes_its_data(as_app):
    as_app.execute(text("UPDATE issues SET priority = 1, updated_at = '2026-11-11T16:00:00Z' WHERE id = 'i3'"))
    as_app.execute(text("INSERT INTO scores (measurable_id, week_start, value) VALUES ('m1', '2026-11-09', 4)"))
    as_app.execute(text("SELECT id FROM todos WHERE id = 't31' FOR NO KEY UPDATE"))  # the routes' row locks
    as_app.execute(text(EVENT))  # history can be appended …
    assert as_app.execute(text("SELECT count(*) FROM audit_events WHERE entity_id = 'role-test'")).scalar() == 1  # … and read
    with Session(bind=as_app) as s:
        assert last_app_write(s).isoformat() == "2026-11-11T16:00:00+00:00"  # startup's clock resume (it swallows errors)


def test_history_cant_be_rewritten_even_with_the_purge_opt_in(as_app):
    as_app.execute(text(EVENT))
    as_app.execute(text("SET LOCAL l10.audit_purge = 'on'"))  # anyone may set it; it only helps a role that may delete
    for sql, why in [("UPDATE audit_events SET actor_id = 'u1'", "permission denied for table audit_events"),
                     ("DELETE FROM audit_events", "permission denied for table audit_events"),
                     ("TRUNCATE audit_events", "permission denied for table audit_events"),
                     ("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete", "must be owner of table audit_events"),
                     ("DROP TABLE audit_events", "must be owner of table audit_events")]:
        refused(as_app, sql, why)


def test_an_event_cant_choose_its_place_in_history(as_app):
    """Append is all it may do: the id, which orders the feed, always comes from the database."""
    cols = "id, team_id, actor_id, action, entity_type, entity_id, entity_label, at"
    vals = "-1, 'team_lead', 'u2', 'issue.solved', 'issue', 'i3', 'forged', '2026-01-01'"
    refused(as_app, f"INSERT INTO audit_events ({cols}) OVERRIDING SYSTEM VALUE VALUES ({vals})",
            "permission denied for table audit_events")
    refused(as_app, f"COPY audit_events ({cols}) FROM STDIN", "permission denied for table audit_events")


def test_people_teams_and_goals_are_read_only_to_the_app(as_app):
    """The app never edits them, and renaming a person would rewrite the actor on every past event."""
    for table, sql in [("users", "UPDATE users SET name = 'Avery Admin' WHERE id = 'u2'"),
                       ("memberships", "INSERT INTO memberships (team_id, user_id) VALUES ('team_sales', 'u5')"),
                       ("measurables", "UPDATE measurables SET goal_value = 0"),
                       ("teams", "UPDATE teams SET name = 'x'")]:
        refused(as_app, sql, f"permission denied for table {table}")


def test_the_app_deletes_nothing_and_leaves_the_schema_alone(as_app):
    for sql, why in [("DELETE FROM todos WHERE id = 't31'", "permission denied for table todos"),
                     ("TRUNCATE issues CASCADE", "permission denied for table issues"),
                     ("UPDATE alembic_version SET version_num = 'x'", "permission denied for table alembic_version"),
                     ("CREATE TABLE sneaky (id int)", "permission denied for schema public"),
                     ("ALTER TABLE issues ADD COLUMN sneaky int", "must be owner of table issues")]:
        refused(as_app, sql, why)


def test_tables_added_later_are_read_only_until_a_migration_grants_more(db):
    db.execute(text("CREATE TABLE later_feature (id serial PRIMARY KEY)"))  # as the owner, like a future migration
    granted = {p: db.execute(text("SELECT has_table_privilege(:r, 'later_feature', :p)"), {"r": ROLE, "p": p}).scalar()
               for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    assert granted == {"SELECT": True, "INSERT": False, "UPDATE": False, "DELETE": False}


def _migration_0009():
    spec = importlib.util.spec_from_file_location("m0009", API_ROOT / "migrations/versions/0009_app_role.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("widen", [f"ALTER ROLE {ROLE} CREATEDB", f"GRANT l10 TO {ROLE}"])
def test_a_role_created_beforehand_must_be_plain(db, widen):
    """An operator may create the role; one with extra powers, or inheriting another role's, is refused."""
    db.execute(text(widen))  # rolled back with the test
    sp = db.begin_nested()
    with pytest.raises(DBAPIError, match="must be a plain role|must not be a member"):
        db.execute(text(_migration_0009().PLAIN_ROLE))
    sp.rollback()


def test_the_password_comes_from_the_environment_and_stays_url_safe(monkeypatch):
    monkeypatch.delenv("L10_APP_DB_PASSWORD", raising=False)
    assert app_role.password() == "l10_app"
    monkeypatch.setenv("L10_APP_DB_PASSWORD", "")  # empty counts as unset, like compose's ${…:-l10_app}
    assert app_role.password() == "l10_app"
    monkeypatch.setenv("L10_APP_DB_PASSWORD", "s3cret-Value_1")
    assert app_role.password() == "s3cret-Value_1"
    monkeypatch.setenv("L10_APP_DB_PASSWORD", "x'; DROP ROLE l10 --")
    with pytest.raises(SystemExit):
        app_role.password()


def test_compose_runs_the_api_as_the_app_role_and_only_tools_as_the_owner():
    services = yaml.safe_load((API_ROOT.parent / "docker-compose.yml").read_text())["services"]

    def user(service):
        return make_url(re.sub(r"\$\{[^}]*\}", "x", services[service]["environment"]["DATABASE_URL"])).username

    assert {s: user(s) for s in ("api", "migrate", "seed")} == {"api": ROLE, "migrate": "l10", "seed": "l10"}
    assert "python -m app.db.app_role" in services["migrate"]["command"][-1]  # login + password, on every `up`
    assert services["db"]["ports"] == ["127.0.0.1:5433:5432"]  # the owner's door: this machine only
    from app.settings import DEFAULT_DATABASE_URL
    assert make_url(DEFAULT_DATABASE_URL).username == ROLE  # an API started without DATABASE_URL isn't the owner either
