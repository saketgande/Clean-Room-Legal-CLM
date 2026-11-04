"""The Obligations page's tiles and its 'Needs a date' filter: GET /obligations/summary and the list's date_state filter
read the same obligations (obligations/service.py visible_obligations), so a tile can never disagree with the list under
it. Real rows in one rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException

from app.auth.models import User
from app.contracts.models import Contract
from app.core.config import settings
from app.core.enums import ContractLifecycleStage
from app.obligations import routes
from app.obligations.dates import EVENT_WAIT, FILTERS, STATES, date_summary
from app.obligations.models import Obligation
from app.obligations.service import ObligationsService
from tests.test_extraction_state import new_world, rolled_back_session

NOT_STATED = "period not stated in the contract - needs a person"
BLOCKS = {
    "waiting_event": {"facts": {"kind": "deadline"}, "reason": f"{EVENT_WAIT}the invoice is received - enter the date"},
    "needs_person": {"facts": {"kind": "deadline"}, "reason": NOT_STATED},
    "no_fixed_date": {"facts": {"kind": "ongoing"}, "reason": None},
}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "disable_rbac", False)  # the dev compose turns RBAC off; the access rule is under test
    with rolled_back_session() as db:
        yield new_world(db)


def _ob(w, state: str, *, contract=None, status="open", **fields) -> Obligation:
    """An obligation in the given date state (not_read: no deadline block at all)."""
    meta = {} if state in ("not_read", "dated") else {"deadline": BLOCKS[state]}
    due = date(2026, 12, 1) if state == "dated" else None
    ob = Obligation(**{"org_id": w.org.id, "contract_id": (contract or w.contract).id, "description": f"{state} duty",
                       "status": status, "due_date": due, "metadata_json": meta, **fields})
    w.db.add(ob)
    w.db.flush()
    return ob


def _list(w, user=None, **kw) -> list[dict]:
    args = {"contract_id": None, "status_filter": None, "due_within_days": None, "date_state": None, "limit": 200,
            "offset": 0, **kw}
    return routes.list_obligations(db=w.db, current_user=user or w.user, **args)


def _summary(w, user=None) -> dict:
    return ObligationsService(w.db).summary(user=user or w.user)


def _other_contract(w, **fields) -> Contract:
    contract = Contract(org_id=w.org.id, title="Other MSA", owner_user_id=w.user.id, contract_type="MSA", **fields)
    w.db.add(contract)
    w.db.flush()
    return contract


def _seed(w):
    for state in ("dated", "waiting_event", "waiting_event", "needs_person", "no_fixed_date", "not_read", "not_read"):
        _ob(w, state)
    _ob(w, "needs_person", status="completed")  # closed work is in no date count and not in a date filter
    _ob(w, "not_read", status="cancelled")
    # What the tracking list never shows: a deleted contract's, a closed contract's, a deleted obligation.
    _ob(w, "needs_person", contract=_other_contract(w, deleted_at=datetime(2026, 10, 1, tzinfo=UTC)))
    _ob(w, "needs_person", contract=_other_contract(w, lifecycle_stage=ContractLifecycleStage.CLOSED))
    _ob(w, "needs_person", deleted_at=datetime(2026, 10, 1, tzinfo=UTC))


def test_the_summary_counts_exactly_what_the_list_shows(world):
    """Guards a tile that says 45 while the list under it shows 43: both come from visible_obligations, so a deleted or
    closed contract, a deleted obligation and a completed one are counted the same way in both."""
    _seed(world)

    summary, everything = _summary(world), _list(world)

    assert summary["total"] == len(everything) == 9
    assert summary["by_status"] == {"open": 7, "due_soon": 0, "overdue": 0, "completed": 1, "cancelled": 1}
    assert summary["dates"] == {"dated": 1, "waiting_event": 2, "needs_person": 1, "no_fixed_date": 1, "not_read": 2}
    assert summary["needs_date"] == 3
    assert summary["line"] == "1 dated, 2 waiting for an event, 3 need a date, 1 no fixed date"
    for state in STATES:
        assert summary["dates"][state] == len(_list(world, date_state=state)), state
    assert summary["needs_date"] == len(_list(world, date_state="needs_date"))


def test_the_filtered_list_is_the_queue_open_obligations_only(world):
    """Guards a finished obligation sitting in the 'Needs a date' queue: completed and cancelled rows are left out."""
    _seed(world)
    queue = _list(world, date_state="needs_date")
    assert {o["date_state"] for o in queue} == {"needs_person", "not_read"}
    assert {o["status"] for o in queue} == {"open"} and len(queue) == 3


def test_paging_applies_after_the_filter(world):
    """Guards 'Showing 200' being 200 rows of which few match: the filter is worked out on read, so it has to come
    before the page is cut."""
    for _ in range(5):
        _ob(world, "not_read")
    for _ in range(4):
        _ob(world, "dated")
    pages = [_list(world, date_state="not_read", limit=2, offset=o) for o in (0, 2, 4)]
    assert [len(p) for p in pages] == [2, 2, 1]
    ids = [o["id"] for p in pages for o in p]
    assert len(set(ids)) == 5 and all(o["date_state"] == "not_read" for p in pages for o in p)


def test_an_unknown_date_filter_is_a_422(world):
    with pytest.raises(HTTPException) as refused:
        _list(world, date_state="overdue")
    assert refused.value.status_code == 422 and "needs_date" in refused.value.detail
    assert set(FILTERS) >= {"needs_date", *STATES}


def test_what_a_person_may_not_open_is_in_neither_the_list_nor_the_tiles(world):
    """Guards the tiles leaking a count of obligations on contracts the viewer cannot see."""
    _ob(world, "needs_person")
    stranger = User(org_id=world.org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Stranger",
                    hashed_password="hash", status="active")
    world.db.add(stranger)
    world.db.flush()

    assert _summary(world, stranger)["total"] == 0 and _list(world, stranger) == []
    assert _summary(world)["total"] == 1


def test_every_row_says_its_date_state_whichever_way_it_is_read(world):
    """Guards the page re-deriving the state from strings: the list, the single read and the update all carry it."""
    ob = _ob(world, "waiting_event")
    assert _list(world)[0]["date_state"] == "waiting_event"
    assert ObligationsService(world.db).serialize_one(ob)["date_state"] == "waiting_event"


def test_the_list_keeps_a_contracts_rows_together_in_a_steady_order(world):
    """Guards paging reshuffling rows: undated rows tie on the due date, so contract, created and id break the tie."""
    other = _other_contract(world)
    for _ in range(3):
        _ob(world, "not_read")
        _ob(world, "not_read", contract=other)
    first = [o["id"] for o in _list(world)]
    assert first == [o["id"] for o in _list(world)]
    contracts = [o["contract_id"] for o in _list(world)]
    assert contracts == sorted(contracts)


def test_the_summary_is_declared_before_the_obligation_id_route():
    """Guards 'summary' being read as an obligation id (a 404 on the page's tiles)."""
    paths = [r.path for r in routes.router.routes]
    assert paths.index("/obligations/summary") < paths.index("/obligations/{obligation_id}")


def test_the_line_matches_the_pages_wording(world):
    assert date_summary({"needs_date": 1}) == "1 needs a date"
