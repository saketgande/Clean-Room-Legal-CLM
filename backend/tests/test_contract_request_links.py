"""Area 2 — the contract <-> request <-> matter triangle.

The audit found the lifecycle was a one-way street: a single FK pointed
request -> contract, and nothing else in the triangle actually connected.

    2.1  a request could not be raised against an existing contract
    2.3  a contract could not name the request that produced it
    2.4  project_id was a bare string with no foreign key
    2.6  a drafted contract was never filed into the request's matter

These are structural assertions — the shape of the schema and the handlers —
because the wiring is the fix. Behavioural coverage of the access rules lives in
test_access_policy.py; this file only proves the links exist and are written.

House style: no database, no fixtures framework.
"""

import inspect

import app.models  # noqa: F401 — registers every table so FKs resolve
from app.contracts.models import Contract
from app.intake.models import IntakeRequest


# ==========================================================================
# 2.1 — a request can be raised against an existing contract
# ==========================================================================

def test_request_create_accepts_a_contract():
    """The create surface can now carry the contract a request is raised on."""
    from app.intake.schemas import RequestCreate

    assert "contract_id" in RequestCreate.model_fields
    assert RequestCreate.model_fields["contract_id"].default is None


def test_create_request_validates_the_contract_before_linking():
    """An unknown or cross-org contract must 404, not silently attach — the same
    ownership check promote() already made."""
    from app.intake import service

    src = inspect.getsource(service.create_request)
    assert "payload.contract_id" in src
    assert "linked_contract.org_id != actor.org_id" in src
    assert "deleted_at is not None" in src
    assert 'raise HTTPException(404, "Contract not found")' in src


def test_create_request_writes_the_reverse_pointer_too():
    """Raising a request against an orphan contract adopts it."""
    from app.intake import service

    src = inspect.getsource(service.create_request)
    assert "linked_contract.intake_request_id = r.id" in src


# ==========================================================================
# 2.3 — the contract knows its originating request
# ==========================================================================

def test_contract_has_a_request_back_pointer():
    col = Contract.__table__.columns.get("intake_request_id")
    assert col is not None, "Contract still cannot name its originating request"
    assert col.nullable is True
    assert col.index is True
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "intake_request"
    assert fk.ondelete == "SET NULL"


def test_both_draft_paths_write_the_reverse_link():
    """The template path AND the attachment path — fixing only one would leave
    every uploaded counterparty paper orphaned."""
    from app.intake import drafting

    src = inspect.getsource(drafting)
    assert src.count("contract.intake_request_id = request.id") == 2


def test_promote_to_contract_also_links_back():
    from app.intake import service

    src = inspect.getsource(service.promote)
    assert "c.intake_request_id = r.id" in src


def test_reverse_link_is_not_settable_by_a_plain_patch():
    """It is written only by the validated linking handlers; a free-form PATCH
    could otherwise re-point a contract at any ticket."""
    from app.contracts.schemas import ContractResponse, ContractUpdate

    assert "intake_request_id" in ContractResponse.model_fields
    assert "intake_request_id" not in ContractUpdate.model_fields


# ==========================================================================
# 2.4 — project_id is a real foreign key
# ==========================================================================

def test_project_id_is_a_real_foreign_key():
    """It used to be a bare VARCHAR while contract_id right beside it was a
    proper FK, so a promoted request could point at a deleted matter."""
    col = IntakeRequest.__table__.columns.get("project_id")
    assert col.foreign_keys, "project_id is still a loose string"
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "project"
    assert fk.ondelete == "SET NULL"


def test_project_id_matches_its_sibling_contract_id():
    """The two links on the same row should behave the same way."""
    proj = next(iter(IntakeRequest.__table__.columns["project_id"].foreign_keys))
    contract = next(iter(IntakeRequest.__table__.columns["contract_id"].foreign_keys))
    assert proj.ondelete == contract.ondelete == "SET NULL"


# ==========================================================================
# 2.6 — a drafted contract is filed into the request's matter
# ==========================================================================

def test_both_draft_paths_forward_the_matter():
    """create_contract_from_upload writes the ProjectContract link when given a
    project_id; both draft paths used to drop it on the floor."""
    from app.intake import drafting

    src = inspect.getsource(drafting)
    assert src.count("project_id=request.project_id") == 2


def test_contract_creation_still_files_into_the_project_when_given_one():
    """The receiving side of 2.6 — unchanged, but it is what makes it work."""
    from app.contract_files import service

    src = inspect.getsource(service.create_contract_from_upload)
    assert "if project_id:" in src
    assert "ProjectContract(" in src


# ==========================================================================
# 2.5 — the promote path is reachable from the UI
# ==========================================================================

def test_promote_is_wired_into_the_intake_ticket():
    """The endpoint and its client method always existed; nothing called them,
    so project_id was always NULL and the request<->matter edge never formed.

    Reads the frontend, because the defect was the absence of a caller — a
    backend-only assertion would have passed the whole time it was broken.
    """
    import pathlib as _p

    root = _p.Path(__file__).resolve().parents[2] / "frontend" / "src"
    ticket = (root / "app" / "(app)" / "intake" / "page.tsx").read_text()
    assert 'intakeApi.promote(id, "project", matterId)' in ticket
    assert "Add to matter" in ticket


def test_raise_request_is_wired_into_the_contract_page():
    """The other missing affordance: a live contract could not seed a ticket."""
    import pathlib as _p

    root = _p.Path(__file__).resolve().parents[2] / "frontend" / "src"
    page = (root / "app" / "(app)" / "contracts" / "[id]" / "page.tsx").read_text()
    assert "contract_id: contract.id" in page
    assert "Raise a request" in page


# ==========================================================================
# The triangle, end to end
# ==========================================================================

def test_every_edge_of_the_triangle_now_exists():
    """A compact statement of the whole area: six links, all present.

    Was: only request -> contract existed.
    """
    edges = {
        "request -> contract": bool(
            IntakeRequest.__table__.columns["contract_id"].foreign_keys
        ),
        "contract -> request": bool(
            Contract.__table__.columns["intake_request_id"].foreign_keys
        ),
        "request -> project": bool(
            IntakeRequest.__table__.columns["project_id"].foreign_keys
        ),
    }
    assert all(edges.values()), edges
