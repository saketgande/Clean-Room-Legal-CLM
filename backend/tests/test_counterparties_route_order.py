"""`GET /counterparties` is registered by both the parties and contracts routers.

The parties one (gated by `intake:create`) must be matched first: Starlette takes
the first route that matches, so if main.py's include order flips, create-only
agent keys would hit the `contract:read` route, get 403, and silently fall onto
the intake `lookup_note` degrade path.
"""

from app.main import create_app
from app.parties.routes import _FILER


def test_parties_counterparty_search_is_matched_before_the_contracts_directory():
    app = create_app()
    matches = [
        r for r in app.routes
        if getattr(r, "path", "").endswith("/counterparties") and "GET" in (r.methods or ())
    ]
    assert len(matches) >= 2, "expected both registrations; update this test if one was removed"
    first = matches[0]
    assert first.endpoint.__module__ == "app.parties.routes"
    assert first.endpoint.__name__ == "list_counterparties"
    assert _FILER in [d.call for d in first.dependant.dependencies]
