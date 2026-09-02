"""CLM lineage inference.

Lineage here is inferred, not asserted, so the tests guard the two ways
inference goes wrong: linking a child to the wrong master, or claiming lineage
where there is none.
"""

from datetime import date

from app.contract_brain.lineage import (
    child_parent_kind,
    infer_parent,
    looks_like_amendment,
)


def _c(id, ctype, cp, eff=None, title=""):
    return {"id": id, "contract_type": ctype, "counterparty_key": cp,
            "effective_date": eff, "title": title}


class TestChildTypes:
    def test_sow_and_dpa_are_children(self):
        assert child_parent_kind("Statement of Work", "")[0] == "statement of work"
        assert child_parent_kind("SoW", "")[0] == "sow"
        assert child_parent_kind("Data Processing Agreement", "")[0] == "data processing agreement"

    def test_type_falls_back_to_title(self):
        # typed 'None' but the title says SoW
        assert child_parent_kind(None, "Acme SOW - Phase 2") is not None

    def test_a_master_is_not_a_child(self):
        assert child_parent_kind("Master Services Agreement", "") is None
        assert child_parent_kind("NDA", "") is None


class TestAmendmentDetection:
    def test_amendment_language(self):
        assert looks_like_amendment("Amendment No. 2", "")
        assert looks_like_amendment(None, "Renewal of MSA-2023-114")
        assert looks_like_amendment("Novation Agreement", "")
        assert not looks_like_amendment("Master Services Agreement", "")


class TestInferParent:
    def test_sow_links_to_same_counterparty_msa(self):
        sow = _c("s1", "Statement of Work", "org:contoso", date(2026, 6, 1))
        msa = _c("m1", "Master Services Agreement", "org:contoso", date(2026, 1, 1))
        other = _c("m2", "Master Services Agreement", "org:acme", date(2026, 1, 1))
        hit = infer_parent(sow, [sow, msa, other])
        assert hit is not None
        parent, conf = hit
        assert parent["id"] == "m1"        # same counterparty wins, not the acme MSA
        assert conf >= 0.8                 # same cp + right type + parent predates

    def test_no_parent_when_counterparty_differs(self):
        sow = _c("s1", "Statement of Work", "org:contoso", date(2026, 6, 1))
        msa = _c("m1", "Master Services Agreement", "org:acme", date(2026, 1, 1))
        assert infer_parent(sow, [sow, msa]) is None

    def test_parent_starting_after_child_is_penalised(self):
        # an MSA that starts after the SoW is a weak parent — should not clear
        # the confidence floor on its own
        sow = _c("s1", "Statement of Work", "org:contoso", date(2026, 1, 1))
        late_msa = _c("m1", "Master Services Agreement", "org:contoso", date(2026, 12, 1))
        hit = infer_parent(sow, [sow, late_msa])
        # 0.6 base - 0.2 date penalty = 0.4, below the 0.5 floor
        assert hit is None

    def test_child_without_counterparty_has_no_parent(self):
        sow = _c("s1", "Statement of Work", None, date(2026, 6, 1))
        msa = _c("m1", "Master Services Agreement", "org:contoso", date(2026, 1, 1))
        assert infer_parent(sow, [sow, msa]) is None

    def test_an_nda_is_never_given_a_parent(self):
        nda = _c("n1", "NDA", "org:contoso", date(2026, 6, 1))
        msa = _c("m1", "Master Services Agreement", "org:contoso", date(2026, 1, 1))
        assert infer_parent(nda, [nda, msa]) is None
