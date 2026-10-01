"""Unauthenticated surface: nothing an anonymous caller sees should reveal who
is registered or map the whole API in production."""

from types import SimpleNamespace

from app import main
from app.auth import routes as auth_routes


def test_register_response_is_identical_for_new_and_existing_emails(monkeypatch):
    """Echoing the new user (but null on a collision) let anyone enumerate
    registered emails and read the org_id."""
    new_user = SimpleNamespace(id="u-1", org_id="org-1")
    register = auth_routes.register.__wrapped__  # past the slowapi limiter
    bodies = []
    for outcome in (new_user, None):
        # The route takes an injected AuthService since the DI refactor.
        auth_service = SimpleNamespace(register_user=lambda payload, o=outcome: ("pending_approval", o))
        bodies.append(register(payload=None, request=None, response=None, auth_service=auth_service))
    assert bodies[0] == bodies[1]
    assert bodies[0]["user"] is None


def test_api_schema_is_not_served_in_production(monkeypatch):
    """/docs and /openapi.json were public in every environment."""
    monkeypatch.setattr(main.settings, "environment", "production")
    # The prod secret checks are covered elsewhere; skip them to build the app.
    monkeypatch.setattr(main, "validate_runtime_settings", lambda s: None)
    app = main.create_app()
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    assert not any(r.path.endswith("/debug/health") for r in app.routes)
