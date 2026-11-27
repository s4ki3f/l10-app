"""Schema lint: every foreign key either has an index starting with its columns, or is listed here on purpose.

Postgres doesn't index foreign keys by itself. The ones the app reads through are indexed (migration 0006);
these are only touched when deleting the referenced row (a user, company or Issue), which the app never does.
A new foreign key fails this test until someone decides which kind it is.
"""
from sqlalchemy import text

DELETE_ONLY = {
    "issues(owner_id)", "measurables(owner_id)", "rocks(owner_id)", "todos(owner_id)",
    "meetings(facilitator_id)", "meeting_attendees(user_id)", "scores(updated_by)",
    "teams(company_id)", "users(company_id)",
    "todos(source_issue_id,team_id)",
}

UNINDEXED_FKS = text("""
SELECT c.conrelid::regclass || '(' || string_agg(a.attname, ',' ORDER BY k.n) || ')'
FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, n)
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f' AND NOT EXISTS (
  SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid
    AND (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1] = c.conkey)
GROUP BY c.conrelid, c.conname
""")


def test_every_foreign_key_is_indexed_or_deliberately_not(db):
    unindexed = set(db.execute(UNINDEXED_FKS).scalars())
    assert unindexed == DELETE_ONLY, {"needs an index or a reason": unindexed - DELETE_ONLY,
                                      "now indexed, drop from the list": DELETE_ONLY - unindexed}
