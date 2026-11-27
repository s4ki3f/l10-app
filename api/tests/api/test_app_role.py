"""Every API test runs as the API's own database role, so each route is exercised with production's rights."""
from sqlalchemy import text


def test_api_tests_run_as_the_apps_least_privilege_role(session):
    assert session.execute(text("SELECT current_user")).scalar() == "l10_app"
