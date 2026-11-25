"""FastAPI app: a documented REST API (OpenAPI at /docs) for both pages."""
from fastapi import FastAPI
from sqlalchemy import text

from .api import errors
from .api.routes import auth, events, issues, meetings, scorecard
from .db.session import get_engine
from .rules.clock import RunningClock, app_now_from_env

app = FastAPI(
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


@app.get("/health", tags=["ops"], summary="Liveness plus database reachability and the app clock")
def health() -> dict:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "now": app.state.clock.now().isoformat()}
