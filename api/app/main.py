"""FastAPI app. Routes arrive with P05/P08; for now the stack boots and reports health."""
from fastapi import FastAPI
from sqlalchemy import text

from .db.session import get_engine
from .rules.clock import RunningClock, app_now_from_env

app = FastAPI(title="L10 API", version="0.1.0", description="Scorecard, Rocks, To-Dos, Issues and the Level 10 Meeting.")
app.state.clock = RunningClock(app_now_from_env())


@app.get("/health", tags=["ops"], summary="Liveness plus database reachability and the app clock")
def health() -> dict:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "now": app.state.clock.now().isoformat()}
