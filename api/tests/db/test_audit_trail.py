"""The audit trail is append-only, enforced by the database — not by convention.

Counts are scoped to this file's own entity id: committed events from other tests stay (append-only), on purpose.
"""
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db.seed import load_seed, reset_to_seed
from tests.conftest import SEED
from tests.db.test_constraints import rejects

ADD = ("INSERT INTO audit_events (team_id, actor_id, action, entity_type, entity_id, entity_label, at) "
       "VALUES ('team_lead', 'u2', 'issue.created', 'issue', 'audit-db-test', 'x', now())")


def mine(db):
    return db.execute(text("SELECT count(*) FROM audit_events WHERE entity_id = 'audit-db-test'")).scalar()


def test_events_cannot_be_edited_deleted_or_truncated(db):
    db.execute(text(ADD))
    rejects(db, "UPDATE audit_events SET actor_id = 'u1' WHERE entity_id = 'audit-db-test'")
    rejects(db, "DELETE FROM audit_events WHERE entity_id = 'audit-db-test'")
    rejects(db, "TRUNCATE audit_events")
    assert mine(db) == 1


def test_ids_come_from_the_database(db):
    sp = db.begin_nested()
    with pytest.raises(ProgrammingError, match="GENERATED ALWAYS|non-DEFAULT"):
        db.execute(text(ADD.replace("(team_id,", "(id, team_id,").replace("VALUES (", "VALUES (999, ")))
    sp.rollback()


def test_shape_is_checked(db):
    rejects(db, ADD.replace("'issue.created'", "'Issue Created'"))
    rejects(db, ADD.replace("'issue', 'audit-db-test'", "'rock', 'audit-db-test'"))
    rejects(db, ADD.replace("entity_label, at)", "entity_label, at, changes)").replace("now())", "now(), '[1]')"))


def test_only_reset_purges_and_only_after_opting_in(db):
    db.execute(text(ADD))
    load_seed(db, SEED)  # a plain rerun keeps history
    assert mine(db) == 1
    reset_to_seed(db, SEED)  # restores exactly the seed, which has no history
    assert db.execute(text("SELECT count(*) FROM audit_events")).scalar() == 0
    db.execute(text(ADD))
    rejects(db, "DELETE FROM audit_events")  # the opt-in ended with the reset


def test_the_app_never_opts_in_to_purging():
    """Only the reset tool executes the opt-in (docs may mention it; code that runs it is what counts)."""
    executes = re.compile(r"""text\(\s*["']SET LOCAL l10\.audit_purge\s*=\s*'on'""")
    app_dir = Path(__file__).resolve().parents[2] / "app"
    users = sorted(str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py") if executes.search(p.read_text()))
    assert users == ["db/seed.py"], users
