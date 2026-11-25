# Decision record

**1. Data model and source links.**
- **Choice.** Store facts and compute statuses. Seed ids are the primary keys.
  - A week is the `date` of its Chicago Monday, a due date is a plain `date`, and a moment is UTC `timestamptz`.
  - An Issue's source is a real foreign key, `source_measurable_id`, plus `source_week`, with a CHECK requiring both or neither. A To-Do links back with `source_issue_id`.
  - Postgres holds every rule it can: one score per cell; one unsolved Issue per cell and one live meeting per team (partial unique indexes); only attendees rate (composite FK); CHECKs on ranges, Monday weeks, solved ⇔ note and goal shape.
- **Rejected.** A JSON source field, which can't be indexed or kept unique. Stored statuses, which go stale.
- **Cost.** Statuses are recomputed per read; at scale they'd be cached per (team, week). Who may own (any Admin, or a Team Member of the team) spans users and memberships, so the API guard checks it, proven by API tests.

**2. API design.**
- **Choice.**
  - Resources are scoped under their team, the security boundary: `/teams/{id}/scorecard|issues|todos|rocks|meetings/active|events`.
  - Record routes take the team from the record, never from the URL.
  - Rule-bearing changes are named actions: make-issue, solve, advance, end, done/undo.
  - Responses carry computed values, so the browser holds no rules.
  - One error shape, `{"error":{"code","message"}}`, with consistent status codes (201/200/401/403/404/409/422).
  - OpenAPI at `/docs`, and TypeScript types are generated from it.
- **Rejected.** Generic PATCH, which hides rules inside field diffs; GraphQL, unneeded at this size.
- **Cost.** Other teams' data returns **404, not 403**, so ids don't leak; a test checks the bodies are identical. An Observer writing in their own team gets 403.

**3. Real-time transport and meeting state.**
- **Choice.** The **database owns the meeting**: section, `section_started_at`, facilitator. Commands go up over REST, and hints come down over **SSE**:
  - `pg_notify` runs inside the writer's transaction, so a hint is sent only if the change commits.
  - One `LISTEN` connection per process fans hints out per team.
  - Browsers refetch through the guarded API, so a missed hint costs nothing.
- **Timer.** It's a start time plus `server_now`; the client corrects its own clock offset. An overrun shows "over time" and never advances.
- **Recovery.** Late join, refresh and reconnect share one path: fetch state, then listen. Control is tied to the user.
- **Stale clicks.** Advance names its `from_section`, so a stale double-click gets 409.
- **Rejected.** WebSockets (traffic down is one-way, while SSE reconnects by itself over HTTP); server memory (lost on restart); Redis (an extra service Postgres already covers here).
- **Cost.** One connection per open tab. The stream releases its DB session after authorizing, so tabs don't hold pool slots.

**4. Duplicate-free Make it an Issue.**
- **Choice.** The database decides.
  - A partial unique index on `(source_measurable_id, source_week) WHERE solved_at IS NULL`, plus `INSERT … ON CONFLICT DO NOTHING RETURNING id`.
  - The request whose insert went through gets **201**; everyone else gets the winner's Issue with **200**. One retry covers the winner being solved between conflict and read.
  - The server recomputes whether the cell is off track (409 if not).
- **Rejected.** Check-then-insert in application code. In the concurrency test (12 threads behind a barrier, real commits), removing the index produced **12 duplicates**; keeping it made the losers fail with 500s.
- **Cost.** Only unsolved Issues are unique, so a cell can raise a new Issue after its old one is solved (the seed's i6).

**5. Unchanging past summaries (described, not built).**
- **Today.** Summaries are computed from current data: the reviewed week is the one before the start, and solved/created items fall within `[started_at, ended_at]`, as in the seed. A later edit, rename or reopen would change history.
- **Fix.** On end, compute the summary once and store it as an immutable JSON snapshot on the meeting, names included. Seeded meetings get theirs at seed time, and reads return the snapshot.
- **Cost.** A snapshot can't be corrected, which is the point of history: corrections become notes. The larger alternative is versioned rows with "as of" reads.

**Proof.** Each safeguard above was removed once in a scratch copy, and every removal failed a test (see `docs/testing.md`).
