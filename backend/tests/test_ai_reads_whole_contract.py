"""Every AI skill read only the first 25,000 characters of a contract. The exam measured the
cost: facts past 25k were found 17% of the time against 72% before it, and the playbook review
reported clauses it never saw as "missing". The whole contract must reach the prompt."""

from app.ai.context import MAX_FULL_TEXT_CHARS, build_contract_context
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract


def _context_for(text: str):
    contract = Contract(
        id="c1", org_id="org1", title="Long MSA", owner_user_id="u1",
        current_authoritative_version_id="v1",
    )
    version = ContractVersion(id="v1", org_id="org1", contract_id="c1", text_snapshot_id="s1")
    snapshot = ContractTextSnapshot(id="s1", org_id="org1", contract_id="c1", text=text)
    rows = {Contract: contract, ContractVersion: version, ContractTextSnapshot: snapshot}

    class _FakeDB:
        def get(self, model, obj_id):
            return rows.get(model)

    return build_contract_context(_FakeDB(), user=None, org_id="org1", contract_id="c1")


def test_a_long_contract_reaches_the_prompt_whole():
    late = "14.2 This Agreement renews automatically for successive one year terms."
    text = "x " * 150_000 + late  # a 300,000-character contract, the clause at the very end
    ctx = _context_for(text)
    assert ctx.text == text
    assert ctx.text_was_truncated is False
    assert late in ctx.text


def test_only_a_document_beyond_the_window_is_cut_and_says_so():
    ctx = _context_for("y" * (MAX_FULL_TEXT_CHARS + 10))
    assert len(ctx.text) == MAX_FULL_TEXT_CHARS
    assert ctx.text_was_truncated is True
    assert ctx.manifest["text_was_truncated"] is True
