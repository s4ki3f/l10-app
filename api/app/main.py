"""FastAPI app: a documented REST API (OpenAPI at /docs) for both pages."""
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import text

from .api import errors
from .api.routes import audit, auth, events, issues, meetings, rocks, scorecard, teams
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
app.include_router(teams.router)
app.include_router(issues.router)
app.include_router(scorecard.router)
app.include_router(meetings.router)
app.include_router(events.router)
app.include_router(rocks.router)
app.include_router(audit.router)


@app.get("/health", tags=["ops"], summary="Liveness plus database reachability and the app clock")
def health() -> dict:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "now": app.state.clock.now().isoformat()}


# ---------- OpenAPI: document the error shape every route really returns ----------

_ERRORS = {
    401: "Not signed in (no, forged, malformed or expired session)",
    403: "Your role can't do this (Observers can read but not change data)",
    404: "Not found, or not in a team you can see (indistinguishable on purpose)",
    409: "Conflicts with current state (e.g. stale_write, issue_solved, meeting_ended, not_off_track)",
    422: "Invalid input (field errors listed in error.fields)",
}
_PUBLIC = {"/auth/users", "/auth/logout", "/health"}


def _openapi() -> dict:
    """FastAPI documents only success responses and a 422 shape we don't use; add the real ErrorOut everywhere."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    from .api.schemas import ErrorOut

    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    error_schema = ErrorOut.model_json_schema(ref_template="#/components/schemas/{model}")
    components.update(error_schema.pop("$defs", {}))
    components["ErrorOut"] = error_schema
    for name in ("HTTPValidationError", "ValidationError"):
        components.pop(name, None)

    ref = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorOut"}}}
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            responses = op.setdefault("responses", {})
            responses.pop("422", None)
            if path in _PUBLIC:
                continue
            if path == "/auth/login":
                codes = (401, 422)
            elif method == "get":
                codes = (401, 404, 422)
            else:
                codes = (401, 403, 404, 409, 422)
            for code in codes:
                resp = responses.setdefault(str(code), {"description": _ERRORS[code]})
                resp.setdefault("content", ref)  # keep a route's own description, but always the real body
    app.openapi_schema = schema
    return schema


app.openapi = _openapi
