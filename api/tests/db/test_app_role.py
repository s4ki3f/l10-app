"""The API's own database role (migration 0009): least privilege, so even the app's credentials can't rewrite
history. The suite connects as the owner (it rebuilds the schema); these tests connect as the app's role."""
import os

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from tests.conftest import API_ROOT, TEST_DATABASE_URL

ROLE = "l10_app"
EVENT = ("INSERT INTO audit_events (team_id, actor_id, action, entity_type, entity_id, entity_label, at) "
         "VALUES ('team_lead', 'u2', 'issue.created', 'issue', 'role-test', 'x', now())")


def app_url():
    return make_url(TEST_DATABASE_URL).set(username=ROLE, password=os.environ.get("L10_APP_DB_PASSWORD", "l10_app"))


@pytest.fixture(scope="module")
def app_engine(engine):  # `engine` rebuilt the test database, migration 0009 included
    eng = create_engine(app_url())
    yield eng
    eng.dispose()


@pytest.fixture
def as_app(app_engine):
    with app_engine.connect() as conn:
        tx = conn.begin()
        yield conn
        tx.rollback()


def refused(conn, sql: str) -> None:
    sp = conn.begin_nested()
    with pytest.raises(ProgrammingError, match="permission denied|must be owner"):
        conn.execute(text(sql))
    sp.rollback()


def test_the_app_reads_and_writes_its_data(as_app):
    as_app.execute(text("UPDATE issues SET priority = 1 WHERE id = 'i3'"))
    as_app.execute(text("INSERT INTO scores (measurable_id, week_start, value) VALUES ('m1', '2026-11-09', 4)"))
    as_app.execute(text("SELECT id FROM todos WHERE id = 't31' FOR NO KEY UPDATE"))  # the routes' row locks
    as_app.execute(text(EVENT))  # history can be appended …
    assert as_app.execute(text("SELECT count(*) FROM audit_events WHERE entity_id = 'role-test'")).scalar() == 1  # … and read


def test_history_cant_be_rewritten_even_with_the_purge_opt_in(as_app):
    as_app.execute(text(EVENT))
    as_app.execute(text("SET LOCAL l10.audit_purge = 'on'"))  # anyone may set it; it only helps a role that may delete
    for sql in ("UPDATE audit_events SET actor_id = 'u1'", "DELETE FROM audit_events", "TRUNCATE audit_events",
                "ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete", "DROP TABLE audit_events"):
        refused(as_app, sql)


def test_the_app_deletes_nothing_and_leaves_the_schema_alone(as_app):
    for sql in ("DELETE FROM todos WHERE id = 't31'", "TRUNCATE issues CASCADE",
                "UPDATE alembic_version SET version_num = 'x'", "CREATE TABLE sneaky (id int)",
                "ALTER TABLE issues ADD COLUMN sneaky int"):
        refused(as_app, sql)


def test_tables_added_later_are_usable_by_the_app(db):
    db.execute(text("CREATE TABLE later_feature (id int PRIMARY KEY)"))  # as the owner, like a future migration
    granted = {p: db.execute(text("SELECT has_table_privilege(:r, 'later_feature', :p)"), {"r": ROLE, "p": p}).scalar()
               for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    assert granted == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}


def test_compose_runs_the_api_as_the_app_role_and_only_tools_as_the_owner():
    services = yaml.safe_load((API_ROOT.parent / "docker-compose.yml").read_text())["services"]

    def user(service):
        url = services[service]["environment"]["DATABASE_URL"].replace("${L10_APP_DB_PASSWORD:-l10_app}", "secret")
        return make_url(url).username

    assert {s: user(s) for s in ("api", "migrate", "seed")} == {"api": ROLE, "migrate": "l10", "seed": "l10"}
