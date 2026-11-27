# L10 — weekly Level 10 meetings

One place for a team's weekly numbers (Scorecard), quarterly Rocks, To-Dos and Issues, and the live Level 10 Meeting that runs through them. **Stack:** Next.js 15 + TypeScript + shadcn/ui + Tailwind · FastAPI · Postgres 16 with Alembic migrations.

## Run it
```bash
docker compose up --build        # db → migrate → seed → api → web
```
Open **http://localhost:3000**, pick a seeded user and open a second browser (or a private window) as someone else. The API docs are at **http://localhost:8000/docs**, and Postgres is on host port 5433. `make reset` wipes the database volume and starts fresh. The seed is safe to rerun: `docker compose run --rm seed` refreshes the seed's own rows and keeps what you created (including which seeded Issues you've solved), and `seed --reset` returns to exactly the seed.

| Try as | Role | Teams |
|---|---|---|
| Avery Admin (u1) | Admin | every team |
| Casey Cho (u3), Blake Brooks (u2) | Team Member | Leadership (+ Sales for Blake) |
| Ellis Evans (u5) | Observer | Leadership, read-only |

## Test
```bash
make test     # every layer, unattended: 263 pytest (rules, DB, API) + 5 Playwright tests (two-browser tests run Chromium + Firefox)
```
Requires Docker, [uv](https://docs.astral.sh/uv/) and pnpm. `make test-api` runs only pytest and `make e2e` only Playwright. **[docs/testing.md](docs/testing.md)** covers the fixture and clock strategy and the committed failure trace (`docs/artifacts/`).

## Rules report
```bash
make rules-report                               # APP_NOW defaults to 2026-11-11T15:00:00Z
APP_NOW=2026-11-23T15:00:00Z make rules-report  # any other date
```
It reseeds a fresh database and prints the JSON for `APP_NOW`. Its output at the default date is committed as **[rules_report.json](rules_report.json)**. Every value comes from the same rule functions the API uses (`api/app/rules/`).

## Bonus: lost-update protection
Scores and Issues carry a `version`. The UI sends the version the user **saw when they started editing**; if someone saved in between, the save is refused with **409 `stale_write`** and the screen says who changed it and to what (*"Blake Brooks changed this to 2.5 a moment ago. Your 3.4 wasn't saved."*). The check is one conditional `UPDATE … WHERE version = :seen` (or an insert-if-absent for a new cell), so it holds under real concurrency: 8 simultaneous saves on one version, exactly one wins. API clients that omit `expected_version` keep the brief's last-save-wins.

## Decisions
See **[docs/decisions.md](docs/decisions.md)** (one page): the data model and source links, API design, real-time transport and recovery, duplicate-free Make it an Issue, and frozen summaries.

## Assumptions
- **Clock.** "Now" comes from one clock. The app's clock starts at `APP_NOW` when the API boots and then moves with real time, so meeting timers tick while "today" stays the seed's day. If the app already ran past `APP_NOW`, a restart resumes just after its latest write, so time never runs backwards. Tests and the rules report freeze it.
- **"As of now".** At any `APP_NOW`, things done, solved or created after it haven't happened yet: the To-Do doesn't count as done, the Issue is still open, and a meeting that ended later isn't reported.
- **Current week's To-Do completion.** The current week is reported like any other week (Nov 9: 3 due, 0 done, 0%). Only a week with nothing due is `null`.
- **Access.** A user who isn't on a team gets **404** for its data (ids don't leak). An Observer writing in their own team gets **403**.
- **Owners.** Any Admin (Admins can do everything a Team Member can, in every team), or a Team Member of that team. Owner pickers list the team's members plus every Admin; Observers own nothing.
- **Make it an Issue** works on an off-track cell inside the 13-week Scorecard. If the cell already has an unsolved Issue, that Issue is returned (200).
- **Scores.** Scores can be entered for any of the 13 weeks, including the current one. The UI protects against lost updates (see Bonus); the API without `expected_version` is last-save-wins.
- **Meeting summaries.** Issues solved and To-Dos created are counted by time window `[started_at, ended_at]`, as with the seeded meetings, so a Team-page change made during a live meeting counts toward it.
- **Ratings** are given in Conclude by attending Admins and Team Members, and can be changed until the end.
- **Advance** carries the section the facilitator saw. A stale double-click gets 409 rather than skipping a section.

## Known limitations
- Summaries are computed on read, not frozen (see decision 5).
- Scores and milestone completion carry no timestamps, so "as of now" can't hide them at earlier dates.
- Security: no CSRF token (relies on a SameSite=Lax cookie and JSON-only bodies). Sessions are stateless and expire after 12 hours, so logout clears the browser's cookie but can't revoke a copied one. `SESSION_SECRET` has a development default, and `COOKIE_SECURE=1` turns on the Secure flag; set both outside local development.
- No facilitator handoff, and no dark-mode toggle.

**At 1,000 companies.** The first pressure points, and the next step for each:

| Pressure point | Next step |
|---|---|
| Computing statuses on every read | Cache per (team, week), invalidated by the same NOTIFY |
| One SSE connection per tab per process | Sticky routing, or move fan-out to a broker |
| NOTIFY payload volume | Channel per company |

Add `company_id` scoping to every query and row-level security in Postgres.

## AI tools
Built with **Claude Code (Claude Opus 5.5)** in the terminal. I set the plan first: 11 problems worked out before any code, each with its data model, flow, failure cases and access rules. Then, problem by problem, the loop was:
1. Claude proposed the design, which I checked against the plan.
2. Claude wrote the code and tests.
3. Each safeguard was **deliberately broken** to prove a test catches it.
4. Small commits.

The hand-worked answer key (`api/tests/rules/answer_key.json`) was written from `seed.json` *before* the rules code, so the tests check the code rather than echo it.

Review caught these, all now fixed and covered by tests:
- a CHECK that let a NULL note through (SQL three-valued logic)
- a route walk that silently tested zero routes on FastAPI 0.165
- a 404 whose message leaked that a record existed
- an untested quarter filter
- an E2E reset that ran stale image code
- an external review: a change landing while a browser was still connecting could be missed. I'd skipped the refetch on the first `hello`, and the fix is listen-then-fetch plus a test that commits a change inside that gap
- a parallel review by three AI reviewer agents (timing, security, rules). It confirmed the graded report and found 19 issues, all verified and fixed with tests. Examples: a seed rerun that could crash `docker compose up`, hints lost while the database listener reconnected, a Rate that could land after End, and inputs that produced a bare 500
- a second external review: a Rate was the one write the clock-resume rule couldn't see. Beyond fixing it, a coverage test now exercises every write route (read from OpenAPI), so a new route can't miss it

Playwright, driven by Claude, was used for the visual review of the UI.
