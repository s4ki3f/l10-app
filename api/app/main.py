"""FastAPI app: a documented REST API (OpenAPI at /docs) for both pages."""
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import text

from .api import errors
from .api.routes import auth, events, issues, meetings, rocks, scorecard
from .db.load import last_app_write
from .db.session import get_engine, session_factory
from .rules.clock import RunningClock, app_now_from_env

@asynccontextmanager
async def lifespan(app: FastAPI):
    """The running clock starts at APP_NOW, or just after the app's latest write if the app already ran past it,
    so a restart never makes recent rows look like the future. Tests install a frozen clock and are left alone."""
    clock = app.state.clock
    if isinstance(clock, RunningClock):
        try:
            with session_factory()() as s:
                last = last_app_write(s)
        except Exception:  # database not reachable yet: keep APP_NOW
            last = None
        start = app_now_from_env()
        if last is not None and last >= start:
            start = last + timedelta(seconds=1)
            logging.getLogger("l10.clock").info("resuming the clock after the last app write: %s", start.isoformat())
        app.state.clock = RunningClock(start)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="L10 API",
    version="0.1.0",
    description="Scorecard, Rocks, To-Dos, Issues and the Level 10 Meeting. "
    "Every error is {\"error\": {\"code\", \"message\"}}. Outsiders get 404, observers writing get 403.",
)
app.state.clock = RunningClock(app_now_from_env())
errors.install(app)
app.include_router(auth.router)
app.include_router(issues.router)
app.include_router(scorecard.router)
app.include_router(meetings.router)
app.include_router(events.router)
app.include_router(rocks.router)


@app.get("/health", tags=["ops"], summary="Liveness plus database reachability and the app clock")
def health() -> dict:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "now": app.state.clock.now().isoformat()}
