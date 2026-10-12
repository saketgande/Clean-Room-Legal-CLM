"""APP-04: an edit without a link to its proposal version is refused, never matched
to "the newest proposal" (which made a different document authoritative)."""

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.contract_files import routes
from app.contract_files.models import ContractEdit


class NoQueryDB:
    def scalar(self, _stmt):
        raise AssertionError("must not guess a proposal version from the database")


def test_unlinked_edit_is_refused_instead_of_guessed():
    edit = ContractEdit(contract_id="c-1", citation=[{"type": "anchor", "start": 0, "end": 4, "matched": True}])
    with pytest.raises(HTTPException) as exc:
        routes._proposal_version_for_edit(NoQueryDB(), edit=edit, org_id="org-1")
    assert exc.value.status_code == 409
