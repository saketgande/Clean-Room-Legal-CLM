"""LLM-02: a risk score comes only from judgments keyed to the clauses that were
sent, reports how much of the contract it covers, and counts each clause type once."""

import asyncio
from types import SimpleNamespace

from app.ai.schemas import ClauseRiskOutput, ContractRiskOutput
from app.contracts import risk


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: self.rows)

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _judgment(ref, level, clause_type="as_echoed"):
    return ClauseRiskOutput(clause_ref=ref, clause_type=clause_type, risk=level, rationale="why", quote="quoted text")


def _score(monkeypatch, clauses, judgments):
    sent = {}

    async def assess(*_args, input_payload, **_kwargs):
        sent.setdefault("batches", []).append(input_payload["clauses"])
        sent["clauses"] = sent["batches"][0]
        return ContractRiskOutput(clause_risks=judgments, summary="overall")

    monkeypatch.setattr(risk.ai_controller, "run_structured_skill", assess)
    contract = SimpleNamespace(id="c-1", org_id="org-1", title="MSA", risk_score=None, risk_band=None,
                               risk_level=None, risk_summary=None, updated_by_user_id=None)
    summary = asyncio.run(risk.compute_contract_risk(FakeDB(clauses), contract=contract, user=SimpleNamespace(id="u-1")))
    return summary, contract, sent


def test_a_partial_answer_is_unknown_and_says_how_much_was_covered(monkeypatch):
    clauses = [SimpleNamespace(clause_type="confidentiality", text=f"Clause {n}") for n in range(40)]
    summary, contract, sent = _score(monkeypatch, clauses, [_judgment(f"C{n}", "low") for n in range(1, 7)])
    assert summary["band"] == "unknown" and contract.risk_score is None
    assert (summary["assessed_count"], summary["clause_count"]) == (6, 40)
    assert "6 of 40" in summary["note"]
    assert sent["clauses"].startswith("[C1] [")


def test_invented_or_repeated_refs_do_not_count_toward_coverage(monkeypatch):
    clauses = [SimpleNamespace(clause_type="confidentiality", text="a"), SimpleNamespace(clause_type="governing_law", text="b")]
    judgments = [_judgment("C1", "low"), _judgment("C1", "high"), _judgment("C9", "high")]
    summary, _, _ = _score(monkeypatch, clauses, judgments)
    assert summary["band"] == "unknown" and summary["assessed_count"] == 1


def test_each_clause_type_counts_once_at_its_worst_and_order_does_not_matter(monkeypatch):
    clauses = [SimpleNamespace(clause_type="limitation_of_liability", text="cap"),
               *[SimpleNamespace(clause_type="confidentiality", text=f"nda {n}") for n in range(5)]]
    judgments = [_judgment("C1", "high")] + [_judgment("[c2]", "low")] + [
        _judgment(f"C{n}", "medium" if n == 4 else "low") for n in range(3, 7)
    ]
    first, contract, _ = _score(monkeypatch, clauses, judgments)
    again, _, _ = _score(monkeypatch, clauses, list(reversed(judgments)))

    liability = risk.canonical_clause_type("limitation_of_liability")
    confidentiality = risk.canonical_clause_type("confidentiality")
    assert (first["assessed_count"], first["coverage"]) == (6, 1.0)
    assert [d["clause_type"] for d in first["drivers"]].count(confidentiality) == 1
    assert next(d for d in first["drivers"] if d["clause_type"] == confidentiality)["risk"] == "medium"
    w_l, w_c = risk.clause_weight(liability), risk.clause_weight(confidentiality)
    assert first["score"] == round(100 * (w_l * 0.95 + w_c * 0.55) / (w_l + w_c)) == again["score"]
    assert contract.risk_level == first["band"]


def test_a_long_contract_is_rated_in_batches_that_merge_into_one_score(monkeypatch):
    """One call for an 82-clause SaaS agreement overran the output limit and the
    score came back "unknown"; batches keep each answer small and still cover
    every clause under its own C-number."""
    clauses = [SimpleNamespace(clause_type="confidentiality", text=f"Clause {n}") for n in range(70)]
    summary, contract, sent = _score(monkeypatch, clauses, [_judgment(f"C{n}", "low") for n in range(1, 71)])
    assert len(sent["batches"]) == 3  # 30 + 30 + 10
    assert sent["batches"][1].startswith("[C31] [") and "[C61]" in sent["batches"][2]
    assert (summary["assessed_count"], summary["coverage"]) == (70, 1.0)
    assert contract.risk_score is not None
