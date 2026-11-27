# Testing

One command, unattended: **`make test`** (needs Docker, `uv`, `pnpm`). It runs every layer:

| Layer | Where | What it proves | Count |
|---|---|---|---|
| Rule tests | `api/tests/rules` | every rule in 2.6 with its boundary cases, one named test per seed trap; the full rules report == a hand-worked answer key; also at a summer date and "as of" Nov 1 | 47 |
| DB tests | `api/tests/db` | Postgres itself rejects invalid rows (ranges, Monday weeks, solved ⇔ note, one open Issue per cell, one live meeting per team, owners are real users, only attendees rate, same-team source links, nothing solved or done before it was created); seed rerun and reset are safe, including after a seeded Issue was solved and re-raised; the clock resumes after the last app write | 27 |
| API tests | `api/tests/api` | 401 / exact 404 (other team) / exact 403 (observer write) on every team-scoped route, walked from OpenAPI with a valid body per route; concurrent Make it an Issue → one Issue; concurrent Starts → one meeting; meeting rules and timer; a live uvicorn SSE test (change reaches another user's stream < 1 s, other teams never hear it); owners (any Admin, or a Team Member of the team); Team page values vs the answer key; "as of now" agrees across list, cell, Make it an Issue, solve and summary; `hello` waits for LISTEN; resync after LISTEN reconnects; a Rate racing an End can't land after it; malformed input is 4xx, never a bare 500; sessions expire; every write route moves the clock-resume point; bonus: stale saves of scores and priority get 409, 8 concurrent saves on one version → exactly one wins; a solve racing a priority change or a second solve (deterministic interleaving) | 191 |
| End-to-end | `web/e2e` | the whole suite runs in Chromium and again in Firefox; inside the two-browser tests, two real browsers (facilitator in Chromium, follower in a separately launched Firefox): the facilitator advances Scorecard → IDS → Conclude and the follower follows within 2 s, both rate, end → follower sees the summary; refresh mid-meeting recovers section + timer; observer is read-only; a change committed while a browser is still connecting still reaches it; bonus: a stale score edit in one browser is refused instead of overwriting the other | 5 (× Chromium, × Firefox) |

## Fixture strategy

- **pytest** uses its own database, `l10_test`, rebuilt at the start of every run: drop schema → `alembic upgrade head` → load `seed.json`. The fixture refuses any database whose name doesn't end in `_test`.
- Each test runs inside a transaction that is rolled back (API calls commit to savepoints inside it), so tests can't see each other's writes.
- Tests that need real concurrency (Make it an Issue race, simultaneous Starts, live SSE) use separate connections that really commit, then delete what they created.
- **Playwright** runs against the real compose stack. `make e2e` first runs `seed --reset`, which deletes everything the app created and restores the seed, so every run starts from the same data.

## Clock strategy

- The app reads "now" only through one clock (`api/app/rules/clock.py`). In the app it starts at `APP_NOW` (default `2026-11-11T15:00:00Z`) and advances with real time, so meeting timers tick while "today" stays the seed's day.
- pytest replaces it with a **frozen clock** at `APP_NOW`; timer tests move the frozen clock (+5 min exactly, +7 min) instead of sleeping.
- Rule tests also run the full report at **2026-08-20** (summer, before the DST change) and **2026-11-01**, to prove nothing is hard-coded to the default date.
- E2E assertions use only relative time (section changes, the timer moving), so they don't depend on the wall clock.

## Proving the tests can fail

Each safeguard was removed once in a scratch copy and the suite rerun; every removal failed at least one test (details per problem in the decision record). Two gaps were found this way and closed: an untested quarter filter on Rocks, and a 404 that leaked whether a record existed in another team.

The method has a blind spot: it removes safeguards that exist, so it can't reveal one that was never written. An external review found such a case (a change landing between a browser's first fetch and its subscription). It now has a test that deliberately acts inside that gap, and I watched that test fail on the old code before fixing it.

## Failure artifact

`docs/artifacts/e2e-failure-sse-blocked.trace.zip` is a real Playwright trace. It was produced by running the two-browser test with the follower's live stream cut after joining:

```
E2E_BREAK_SSE=1 pnpm --dir web exec playwright test -g "second browser follows"
```

The facilitator (Chromium) moves to IDS; the follower (Firefox) stays on Scorecard with the indicator showing "Reconnecting…", and the test fails with `Expected "ids", Received "scorecard"` after 2 s. The trace covers both browsers. Open it with `pnpm --dir web exec playwright show-trace ../docs/artifacts/e2e-failure-sse-blocked.trace.zip` (or drop it on trace.playwright.dev). Screenshots of both browsers at the moment of failure are next to it.
