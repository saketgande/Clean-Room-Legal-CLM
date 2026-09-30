"""Proves the contracts/ dependency-injection conversion (see
backend/DI_MIGRATION.md): a route's service dependency can be swapped for a
fake via FastAPI's dependency_overrides, without monkeypatching module
internals — the pattern the rest of the codebase's DI conversion follows.
"""

from typing import ClassVar

from app.contracts.dependencies import get_contract_service
from app.core.deps import get_current_user


class _FakeContract:
    """Enough attributes to satisfy ContractResponse's from_attributes model —
    a real ContractService would return an ORM row with all of these."""

    id = "contract-1"
    org_id = "org-1"
    title = "Fake NDA"
    contract_type = None
    lifecycle_stage = "intake"
    renewal_due = False
    archived = False
    owner_user_id = "user-1"
    counterparty_name = None
    jurisdiction = None
    confidentiality = "internal"
    risk_level = None
    risk_score = None
    risk_band = None
    risk_summary = None
    value_amount = None
    currency = None
    effective_date = None
    expiration_date = None
    current_contract_file_id = None
    current_authoritative_version_id = None
    metadata_json: ClassVar[dict] = {}


class _FakeContractService:
    """Stands in for ContractService — records the call, returns canned data."""

    def __init__(self):
        self.calls: list[dict] = []

    def get_contract_for_user(self, *, contract_id: str, user):
        self.calls.append({"contract_id": contract_id, "user_id": getattr(user, "id", None)})
        return _FakeContract()


class _FakeUser:
    id = "user-1"
    org_id = "org-1"
    permission_values: ClassVar[list[str]] = ["contract:read"]


def test_get_contract_route_uses_injected_service(client):
    """GET /contracts/{id} should call whatever ContractService the DI
    container hands it — proven by overriding get_contract_service with a
    fake and asserting the fake, not a real DB-backed instance, was used."""
    fake_service = _FakeContractService()
    fake_user = _FakeUser()

    client.app.dependency_overrides[get_contract_service] = lambda: fake_service
    client.app.dependency_overrides[get_current_user] = lambda: fake_user

    response = client.get("/api/v1/contracts/contract-1")

    assert response.status_code == 200
    assert fake_service.calls == [{"contract_id": "contract-1", "user_id": "user-1"}]


def test_get_contract_risk_route_reads_from_injected_contract(client):
    """/contracts/{id}/risk falls back to the contract's own risk fields when
    no summary is stored yet — still true after the DI conversion."""
    fake_service = _FakeContractService()
    fake_user = _FakeUser()

    client.app.dependency_overrides[get_contract_service] = lambda: fake_service
    client.app.dependency_overrides[get_current_user] = lambda: fake_user

    response = client.get("/api/v1/contracts/contract-1/risk")

    assert response.status_code == 200
    body = response.json()
    assert body["note"] == "Not computed yet."
    assert fake_service.calls == [{"contract_id": "contract-1", "user_id": "user-1"}]
