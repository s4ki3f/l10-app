"""Server-Sent Events: one stream per team the browser is viewing.

Authorization happens once, at connect, with a short-lived DB session that is released before streaming:
an open tab must not hold a pooled connection. After that the stream carries only {"topic": ...} hints.
On (re)connect the browser refetches everything, so late joins, refreshes and reconnects share one path.
"""
import asyncio
import inspect
import json

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import StreamingResponse

from ...realtime import broker
from ..deps import SESSION_COOKIE, current_user, get_db, team_reader

router = APIRouter(tags=["realtime"])
HEARTBEAT_SECONDS = 15


def _authorize(request: Request, team_id: str, token: str | None) -> None:
    provider = request.app.dependency_overrides.get(get_db, get_db)
    made = provider()
    is_gen = inspect.isgenerator(made)
    db = next(made) if is_gen else made
    try:
        team_reader(team_id, current_user(db, token), db)
    finally:
        if is_gen:
            made.close()  # release the pooled connection before streaming starts


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get(
    "/teams/{team_id}/events",
    summary="Live change hints for a team (text/event-stream). Refetch the named topic when one arrives.",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def team_events(request: Request, team_id: str, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    await asyncio.to_thread(_authorize, request, team_id, token)
    sub = broker.subscribe(team_id)

    async def stream():
        try:
            yield "retry: 1000\n\n"  # browsers reconnect after 1 s if the stream drops
            yield _sse("hello", {"team_id": team_id, "server_now": request.app.state.clock.now().isoformat()})
            while not await request.is_disconnected():
                try:
                    message = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield _sse("change", {k: v for k, v in message.items() if k != "team_id"})
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
