"""The documented API matches the real one: every error response uses the ErrorOut shape the handlers return."""
from app.main import app

ERROR_REF = "#/components/schemas/ErrorOut"


def ops():
    for path, methods in app.openapi()["paths"].items():
        for method, op in methods.items():
            yield path, method, op


def test_no_framework_default_error_shape_is_documented():
    schemas = app.openapi()["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and "ErrorOut" in schemas


def test_every_documented_error_uses_error_out():
    for path, method, op in ops():
        for code, resp in op["responses"].items():
            if code.startswith(("4", "5")):
                assert resp["content"]["application/json"]["schema"]["$ref"] == ERROR_REF, (method, path, code)


def test_team_routes_document_their_guard():
    for path, method, op in ops():
        if "{" in path:  # every route with an id goes through the guard
            assert {"401", "404", "422"} <= set(op["responses"]), (method, path)
            if method != "get":
                assert "403" in op["responses"], (method, path)


def test_a_documented_error_matches_a_real_one(as_user):
    r = as_user("u2").post("/teams/team_lead/issues", json={})
    assert set(r.json()) == {"error"} and {"code", "message", "fields"} <= set(r.json()["error"])
