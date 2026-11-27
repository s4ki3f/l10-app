"""One error shape for every response: {"error": {"code": "...", "message": "..."}}.

Status codes, used the same way everywhere:
201 created · 200 returned an existing one · 401 not signed in · 403 your role can't do this ·
404 can't see it or doesn't exist (indistinguishable on purpose) · 409 conflicts with current state ·
422 invalid input.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        self.status, self.code, self.message, self.extra = status, code, message, extra


def not_found(what: str = "resource") -> AppError:
    return AppError(404, "not_found", f"{what} not found")


def forbidden(message: str = "your role can't do this") -> AppError:
    return AppError(403, "forbidden", message)


def unauthenticated() -> AppError:
    return AppError(401, "not_signed_in", "sign in first")


def invalid(code: str, message: str) -> AppError:
    return AppError(422, code, message)


def conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


def _body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, **exc.extra), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("invalid_input", "request failed validation", fields=fields), status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        # last line of defence: keep the one error shape and never leak a stack trace to the client
        logging.getLogger("l10.api").exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(_body("internal_error", "something went wrong on our side"), status_code=500)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)
