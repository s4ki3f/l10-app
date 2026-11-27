# Decision record

**1. Data model and source links.**
- **Choice.** Store facts and compute statuses; seed ids are the primary keys.
  - A week is the `date` of its Chicago Monday, a due date is a plain `date`, and a moment is UTC `timestamptz`.
  - An Issue's source is a real foreign key (`source_measurable_id`) plus `source_week`, both or neither. A To-Do links back with `source_issue_id`, and source links must stay in the same team (composite FKs).
  - Postgres holds every rule it can:
    - partial unique indexes: one unsolved Issue per cell, one live meeting per team;
    - one score per cell, and only attendees rate;
    - CHECKs on ranges, Monday weeks, solved ⇔ note, goal shape, and nothing solved or done before it was created.
- **Rejected.** A JSON source field (can't be indexed or kept unique) and stored statuses (they go stale).
- **Cost.** Statuses are recomputed on every read; at scale they'd be cached per (team, week). Who may own an item spans users and memberships, so the API guard checks that rule. Foreign keys on read paths are indexed; a schema test makes every new foreign key an explicit index-or-not decision.

**2. API design.**
- **Choice.**
  - Resources are scoped under their team, the security boundary. Record routes take the team from the record, never the URL.
  - Changes that carry rules are named actions: make-issue, solve, advance, end, done/undo.
  - Responses carry computed values, so the browser holds no rules.
  - One error shape, `{"error":{"code","message"}}`, with status codes used consistently.
  - OpenAPI at `/docs` documents that error shape on every route (a contract test enforces it); the frontend's TypeScript types, errors included, are generated from it. References are typed for what they point at (`ItemRef {id, title}` for Issues and To-Dos, `PersonRef` for people).
- **Rejected.** Generic PATCH, which hides rules inside field diffs; GraphQL, unneeded at this size.
- **Cost.** Other teams' data returns **404, not 403**, so ids don't leak. A test checks that the response bodies are identical. An Observer writing in their own team gets 403.

**3. Real-time transport and meeting state.**
- **Choice.** The **database owns the meeting** (section, `section_started_at`, facilitator). Commands go up over REST; hints come down over **SSE**:
  - `pg_notify` runs inside the writer's transaction, so a hint is sent only if the change commits.
  - One `LISTEN` per process fans hints out to each team. Browsers refetch through the guarded API.
- **Timer.** A start time plus `server_now`, so a wrong laptop clock doesn't matter. An overrun shows "over time" and never advances.
- **Recovery.** **Listen, then fetch**: `hello` is sent only once the browser is subscribed and LISTEN is up, and every `hello` triggers a refetch. If LISTEN drops, every stream is told to resync, and new streams get 503 until it's back.
- **Races.** Advance names its `from_section` (a stale double-click gets 409). Meeting commands lock the meeting row, so a Rate can't land after an End it raced with.
- **Rejected.** WebSockets (traffic down is one-way, and SSE reconnects on its own); server memory (lost on restart); Redis (Postgres covers it here).
- **Cost.** One connection per open tab. The stream releases its database session after authorizing.

**4. Duplicate-free Make it an Issue.**
- **Choice.** The database decides. The partial unique index on `(source_measurable_id, source_week) WHERE solved_at IS NULL` is combined with `INSERT … ON CONFLICT DO NOTHING RETURNING id`.
  - The request whose insert went through gets **201**; everyone else gets the winner's Issue with **200**.
  - The server recomputes whether the cell is off track (409 if not). Writes to one cell also take that cell's lock (it may have no score row to lock yet), so clicks on a cell queue; the index stays the guarantee.
- **Rejected.** Check-then-insert in code. In the concurrency test (12 threads, real commits), removing the index produced **12 duplicates**.
- **Cost.** Only unsolved Issues are unique, so a cell can raise a new Issue after the old one is solved (the seed's i6).

**5. Unchanging past summaries (described, not built).**
- **Today.** A summary is computed from current data. The reviewed week is the week before the meeting started, and solved and created items are those inside `[started_at, ended_at]`. A later edit or reopen would change history.
- **Fix.** When the meeting ends, compute the summary once and store it on the meeting as an immutable JSON snapshot, names included. Seeded meetings get theirs at seed time.
- **Cost.** A snapshot can't be corrected, and that's the point of history: corrections become notes. The larger alternative is versioned rows queried "as of" a time.

**Proof.** Each safeguard was removed once in a scratch copy, and every removal failed a test (see `docs/testing.md`).
