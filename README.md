# L10 — weekly Level 10 meetings

One place for a team's weekly numbers (Scorecard), quarterly Rocks, To-Dos and Issues, and the live Level 10 Meeting that runs through them. **Stack:** Next.js 15 + TypeScript + shadcn/ui + Tailwind · FastAPI · Postgres 16 with Alembic migrations.

## Run it
```bash
docker compose up --build        # db → migrate → seed → api → web
```
Open **http://localhost:3000**, pick a seeded user, and open a second browser (or private window) as someone else. API docs: **http://localhost:8000/docs**; Postgres on port 5433. The seed is safe to rerun (`docker compose run --rm seed` keeps your changes); `seed --reset` restores exactly the seed, and `make reset` wipes the volume.

| Try as | Role | Teams |
|---|---|---|
| Avery Admin (u1) | Admin | every team |
| Casey Cho (u3), Blake Brooks (u2) | Team Member | Leadership (+ Sales for Blake) |
| Ellis Evans (u5) | Observer | Leadership, read-only |

## Test
```bash
make test     # every layer, unattended: 358 pytest (rules, DB, API) + 8 Playwright tests, run in Chromium and in Firefox
```
Requires Docker, [uv](https://docs.astral.sh/uv/) and pnpm (`make test-api` / `make e2e` run one layer). **[docs/testing.md](docs/testing.md)**: fixture and clock strategy, and the committed failure trace in `docs/artifacts/`.

## Rules report
```bash
make rules-report                               # APP_NOW defaults to 2026-11-11T15:00:00Z
APP_NOW=2026-11-23T15:00:00Z make rules-report  # any other date
```
It reseeds a database and prints the JSON for `APP_NOW`; the default-date output is committed as **[rules_report.json](rules_report.json)**. The API uses the same rule functions (`api/app/rules/`).

## Bonus
- **Lost-update protection.** Scores and Issues carry a `version`; the UI sends the one the user **saw when they started editing**, and a stale save gets **409 `stale_write`** saying who changed it (*"Blake Brooks changed this to 2.5 a moment ago. Your 3.4 wasn't saved."*). One conditional `UPDATE … WHERE version = :seen` decides (8 simultaneous saves → one winner).
- **Make it an Issue from an off-track Rock or an overdue To-Do**, with the same no-duplicates rule and code path; eligibility is checked on the server under a lock.
- **Audit trail.** Every change records who, what (before → after) and when, in the same transaction; refused writes and no-ops record nothing. `audit_events` is append-only twice over: a trigger rejects UPDATE, DELETE and TRUNCATE, and the API runs as a least-privilege database role: it can append events (never choosing their id or place) but not change or remove them, writes only the tables it must, and owns nothing. Only `seed --reset`, as the owner, may purge it, by opt-in. Read it at `GET /teams/{id}/audit` (paginated, or one record's history) or in the live **Activity** card; a coverage test makes every write route record exactly one event.

## Decisions
**[docs/decisions.md](docs/decisions.md)** (one page): data model, API design, real-time and recovery, duplicate-free Make it an Issue, frozen summaries.

## Assumptions
- **Clock.** Starts at `APP_NOW` and moves with real time (timers tick, "today" stays the seed's day); after a restart it resumes after the latest write. Tests and the rules report freeze it.
- **"As of now".** Anything done, solved or created after `APP_NOW` hasn't happened yet, in the report and the API alike.
- **Current week's To-Do completion** is reported like any other week (Nov 9: 3 due, 0 done, 0%); only a week with nothing due is `null`.
- **Access.** Not on the team → **404** (ids don't leak); an Observer writing in their own team → **403**.
- **Owners.** Any Admin (in every team) or a Team Member of that team; Observers own nothing.
- **Make it an Issue** works on an off-track cell of the 13-week Scorecard and returns an existing unsolved one (200). **Scores** can be entered for any of those weeks.
- **Meeting summaries** count what was solved or created inside `[started_at, ended_at]`, as the seeded meetings do. **Ratings** happen in Conclude, by attending Admins and Team Members.

## Known limitations
- Summaries are computed on read, not frozen (see decision 5).
- Scores and milestone completion carry no timestamps, so "as of now" can't hide them at earlier dates.
- Audit trail: the app writes its own events, so the API's credentials could add a false one or change data without one (never alter a recorded one). Event ids share one sequence: gaps hint at how busy other teams are.
- Security: no CSRF token (SameSite=Lax cookie, JSON-only bodies). Sessions are stateless and expire after 12 h, so logout can't revoke a copied cookie. Outside local development set `POSTGRES_PASSWORD` (before the first `up`), `L10_APP_DB_PASSWORD`, `SESSION_SECRET` and `COOKIE_SECURE=1`.
- No facilitator handoff, and no dark-mode toggle.

**At 1,000 companies** (a design note, not a load test): cache statuses per (team, week), invalidated by the same NOTIFY; sticky routing or a broker for SSE fan-out; a NOTIFY channel per company; `company_id` scoping on every query plus Postgres row-level security.

## AI tools
Built with **Claude Code (Claude Opus 5.5)**. I set the plan first (11 problems, each with data model, flow, failure cases and access rules), then per problem: Claude proposed the design and I checked it against the plan; Claude wrote code and tests; each safeguard was **deliberately broken** to prove a test catches it; small commits. The answer key (`api/tests/rules/answer_key.json`) was hand-worked from `seed.json` *before* the rules code, so tests check the code rather than echo it. Playwright, driven by Claude, did the visual review.

Reviews found these; each is fixed with a test:
- **Break-it / self-review:** a CHECK that let a NULL note through; a route walk testing zero routes; a 404 leaking a record's existence; an untested quarter filter; an E2E reset running stale image code.
- **External reviews:** a change landing while a browser was still connecting (now listen-then-fetch); a solve racing a priority change (the check now lives in the UPDATE); race tests that slept instead of waiting on the lock; an API whose database role could purge its own history (now least-privilege).
- **Parallel AI reviewers** (before merges): confirmed the graded report; found e.g. hints lost while the database listener reconnected, a Rate landing after End, a double-click that ticked a To-Do, and seed reruns silently undoing audited changes.
