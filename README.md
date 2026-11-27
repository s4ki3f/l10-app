# L10 — weekly Level 10 meetings

One place for a team's weekly numbers (Scorecard), quarterly Rocks, To-Dos and Issues, and the live Level 10 Meeting that runs through them. **Stack:** Next.js 15 + TypeScript + shadcn/ui + Tailwind · FastAPI · Postgres 16 with Alembic migrations.

## Run it
```bash
docker compose up --build        # db → migrate → seed → api → web
```
Open **http://localhost:3000**, pick a seeded user, and open a second browser (or private window) as someone else. API docs: **http://localhost:8000/docs**; Postgres on host port 5433. The seed is safe to rerun (`docker compose run --rm seed` keeps your changes); `seed --reset` restores exactly the seed, and `make reset` wipes the volume.

| Try as | Role | Teams |
|---|---|---|
| Avery Admin (u1) | Admin | every team |
| Casey Cho (u3), Blake Brooks (u2) | Team Member | Leadership (+ Sales for Blake) |
| Ellis Evans (u5) | Observer | Leadership, read-only |

## Test
```bash
make test     # every layer, unattended: 265 pytest (rules, DB, API) + 5 Playwright tests, run in Chromium and in Firefox
```
Requires Docker, [uv](https://docs.astral.sh/uv/) and pnpm (`make test-api` / `make e2e` run one layer). **[docs/testing.md](docs/testing.md)**: fixture and clock strategy, and the committed failure trace in `docs/artifacts/`.

## Rules report
```bash
make rules-report                               # APP_NOW defaults to 2026-11-11T15:00:00Z
APP_NOW=2026-11-23T15:00:00Z make rules-report  # any other date
```
It reseeds a fresh database and prints the JSON for `APP_NOW`; the default-date output is committed as **[rules_report.json](rules_report.json)**. The API uses the same rule functions (`api/app/rules/`).

## Bonus: lost-update protection
Scores and Issues carry a `version`. The UI sends the version the user **saw when they started editing**; if someone saved in between, the save gets **409 `stale_write`** and the screen says who changed it (*"Blake Brooks changed this to 2.5 a moment ago. Your 3.4 wasn't saved."*). One conditional `UPDATE … WHERE version = :seen` decides, so 8 simultaneous saves on one version give exactly one winner. Without `expected_version` the API keeps last-save-wins.

## Decisions
**[docs/decisions.md](docs/decisions.md)** (one page): data model, API design, real-time and recovery, duplicate-free Make it an Issue, frozen summaries.

## Assumptions
- **Clock.** One clock: it starts at `APP_NOW` and moves with real time, so timers tick while "today" stays the seed's day. After a restart it resumes just after the app's latest write, so time never runs backwards. Tests and the rules report freeze it.
- **"As of now".** Anything done, solved or created after `APP_NOW` hasn't happened yet, in the report and the API alike.
- **Current week's To-Do completion.** The current week is reported like any other week (Nov 9: 3 due, 0 done, 0%). Only a week with nothing due is `null`.
- **Access.** A user who isn't on a team gets **404** for its data (ids don't leak). An Observer writing in their own team gets **403**.
- **Owners.** Any Admin (they can do everything a Team Member can, in every team), or a Team Member of that team; Observers own nothing.
- **Make it an Issue** works on an off-track cell inside the 13-week Scorecard. If the cell already has an unsolved Issue, that Issue is returned (200).
- **Scores** can be entered for any of the 13 weeks, including the current one.
- **Meeting summaries** count Issues solved and To-Dos created inside `[started_at, ended_at]`, as the seeded meetings do, so a Team-page change during a live meeting counts toward it.
- **Ratings** happen in Conclude, by attending Admins and Team Members, changeable until the end.

## Known limitations
- Summaries are computed on read, not frozen (see decision 5).
- Scores and milestone completion carry no timestamps, so "as of now" can't hide them at earlier dates.
- Security: no CSRF token (SameSite=Lax cookie, JSON-only bodies). Sessions are stateless and expire after 12 h, so logout can't revoke a copied cookie. Set `SESSION_SECRET` and `COOKIE_SECURE=1` outside local development.
- No facilitator handoff, and no dark-mode toggle.

**At 1,000 companies.** The first pressure points, and the next step for each:

| Pressure point | Next step |
|---|---|
| Computing statuses on every read | Cache per (team, week), invalidated by the same NOTIFY |
| One SSE connection per tab per process | Sticky routing, or move fan-out to a broker |
| NOTIFY payload volume | Channel per company |

Add `company_id` scoping to every query and row-level security in Postgres.

## AI tools
Built with **Claude Code (Claude Opus 5.5)**. I set the plan first (11 problems, each with data model, flow, failure cases and access rules), then per problem: Claude proposed the design and I checked it against the plan; Claude wrote code and tests; each safeguard was **deliberately broken** to prove a test catches it; small commits. The answer key (`api/tests/rules/answer_key.json`) was hand-worked from `seed.json` *before* the rules code, so tests check the code rather than echo it. Playwright, driven by Claude, did the visual review.

Reviews found these; each is fixed with a test:
- **Break-it / self-review:** a CHECK that let a NULL note through; a route walk that tested zero routes; a 404 that leaked a record's existence; an untested quarter filter; an E2E reset running stale image code.
- **External review:** a change landing while a browser was still connecting could be missed (now listen-then-fetch, with a test acting inside that gap); later, a Rate that the clock-resume rule couldn't see (now a test covers every write route from OpenAPI); then a solve racing a priority change (the "still unsolved" check now lives in the UPDATE) and an E2E test that depended on what an earlier run left behind.
- **Three parallel AI reviewers** (timing, security, rules): confirmed the graded report; 19 findings, e.g. a seed rerun that could crash `docker compose up`, hints lost while the database listener reconnected, a Rate landing after End, inputs giving a bare 500.
