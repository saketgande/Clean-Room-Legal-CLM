"""Regression guard: a screen-level check must never be injected as a service.

``require_screen_level(...)`` (app/core/deps.py) returns a ``ResolvedScreenAccess`` --
the *result of the permission check* -- not a service object. A route must receive
it under the throw-away name ``_screen`` and get its service from the module's own
``get_*_service`` provider.

Writing ``service: XService = Depends(_X_EDIT)`` instead makes FastAPI hand the
permission result to the route as ``service``. The screen check passes, then the
first ``service.<method>()`` call crashes with
``AttributeError: 'ResolvedScreenAccess' object has no attribute ...`` (HTTP 500).
That exact mistake shipped on 8 trademarks routes and nothing caught it: the other
screen-level tests call the ``Depends(...)`` guard objects directly and never run a
route body.

Scope: trademarks only. The notices router has the same mistake on 11 routes
(``service: NoticesService = Depends(_NOTICES_EDIT|ADD|DELETE)``); it has not been
fixed yet, so it is deliberately not covered here. Once it is fixed, widen
``_PREFIX`` handling to the whole app.

These tests inspect the real app's routing table, so they need no database.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import app
from app.trademarks.dependencies import (
    get_trademark_clearance_service,
    get_trademark_comment_service,
    get_trademarks_service,
)

_PREFIX = "/api/v1/trademarks"


def _is_screen_guard(call) -> bool:
    return getattr(call, "__qualname__", "").startswith("require_screen_level")


def _trademarks_routes():
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(_PREFIX)]


def test_trademarks_screen_guards_are_only_ever_injected_as__screen():
    offenders: list[str] = []
    guarded = 0
    for route in _trademarks_routes():
        for dep in route.dependant.dependencies:
            if _is_screen_guard(dep.call):
                guarded += 1
                if dep.name != "_screen":
                    offenders.append(
                        f"{min(route.methods)} {route.path}: guard injected as "
                        f"'{dep.name}' (use '_screen=Depends(...)' and a get_*_service provider)"
                    )
    assert guarded > 0, "no screen guards found - this test no longer inspects anything"
    assert not offenders, (
        "screen guard used as something other than _screen:\n  " + "\n  ".join(offenders)
    )


def test_every_trademarks_route_gets_its_service_from_a_trademarks_provider():
    # The comments and clearance routes legitimately use their own providers.
    providers = set()
    for route in _trademarks_routes():
        for dep in route.dependant.dependencies:
            if dep.name == "service":
                providers.add(dep.call)
    assert providers, "no trademarks routes with a 'service' dependency were found"
    assert providers == {get_trademarks_service, get_trademark_comment_service, get_trademark_clearance_service}
