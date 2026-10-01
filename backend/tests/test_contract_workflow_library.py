"""The starter workflows route every form request to exactly one workflow.

Guards the failure the library exists to prevent: a request of some agreement
type landing on no workflow (it waits silently) or on two at once with nothing
to tell them apart (the choice comes down to a tie-break nobody meant) — e.g. a
value tier with a gap at its boundary, or two NDA workflows with equally
specific conditions. Fast-Track ("needed within 7 days") is meant to win over
the standard and one-way NDA workflows; the cases pin that it does.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.core.database import utcnow
from app.workflows import service
from app.workflows.builtin import BUILTIN_FLOWS

_FLOWS = [SimpleNamespace(name=s["name"], eval_order=s["eval_order"],
                          criteria={"used_for": s["used_for"], "conditions": s.get("conditions", [])})
          for s in BUILTIN_FLOWS if s.get("used_for")]


def _pick(fv: dict) -> str | None:
    """What the engine picks, flows in their priority order as _enabled_flows gives them."""
    f = service._pick_used_for(sorted(_FLOWS, key=lambda x: x.eval_order), SimpleNamespace(field_values=fv))
    return f.name if f else None


def _in(days: int) -> str:
    return (utcnow().date() + timedelta(days=days)).isoformat()


_NEW = "new_agreement"
CASES = [
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Our template", "nda_kind": "Mutual"}, "NDA — standard"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Our template", "nda_kind": "Mutual",
      "needed_by": _in(30)}, "NDA — standard"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Our template", "nda_kind": "Mutual",
      "needed_by": _in(7)}, "NDA Fast-Track"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Our template", "nda_kind": "Mutual",
      "needed_by": _in(-2)}, "NDA Fast-Track"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Our template", "nda_kind": "One-way"}, "NDA — one-way"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Our template", "nda_kind": "One-way",
      "needed_by": _in(3)}, "NDA Fast-Track"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Their paper", "nda_kind": "Mutual",
      "needed_by": _in(2)}, "NDA — their paper"),
    ({"request_form": _NEW, "agreement_type": "NDA", "paper": "Their paper", "nda_kind": "Mutual"}, "NDA — their paper"),
    ({"request_form": _NEW, "agreement_type": "Services (MSA)", "value": 4_999_999}, "Master Services Agreement"),
    ({"request_form": _NEW, "agreement_type": "Services (MSA)", "value": 5_000_000}, "MSA — high value"),
    ({"request_form": _NEW, "agreement_type": "Buying from a vendor", "value": 499_999}, "Vendor — small purchase"),
    ({"request_form": _NEW, "agreement_type": "Buying from a vendor", "value": 500_000}, "Vendor — standard purchase"),
    ({"request_form": _NEW, "agreement_type": "Software or SaaS", "value": 10}, "Software / SaaS purchase"),
    ({"request_form": _NEW, "agreement_type": "Consultancy", "value": 10}, "Consultancy"),
    ({"request_form": _NEW, "agreement_type": "Selling to a customer", "value": 9_999_999}, "Customer — standard"),
    ({"request_form": _NEW, "agreement_type": "Selling to a customer", "value": 10_000_000}, "Customer — strategic deal"),
    ({"request_form": _NEW, "agreement_type": "Something else"}, "Other agreement"),
    ({"request_form": "sow", "value": 10}, "Statement of Work"),
    ({"request_form": "dpa"}, "Data Processing Agreement"),
    ({"request_form": "amendment"}, "Amendment"),
    ({"request_form": "renewal", "renew_terms": "Same terms"}, "Renewal — same terms"),
    ({"request_form": "renewal", "renew_terms": "Changed terms"}, "Renewal — changed terms"),
    ({"request_form": "termination", "grounds": "Breach by the other side"}, "Termination — for breach"),
    ({"request_form": "termination", "grounds": "For convenience"}, "Termination — other grounds"),
    ({"request_form": "novation"}, "Novation"),
    ({"request_form": "regularize"}, "Signed outside the system"),
]


@pytest.mark.parametrize(("fv", "expected"), CASES, ids=[c[1] for c in CASES])
def test_each_request_fits_exactly_one_workflow(fv, expected):
    assert _pick({"currency": "INR", **fv}) == expected


def test_every_form_and_kind_has_a_workflow():
    """Nothing on the request page routes to "no workflow" out of the box."""
    from app.intake.agreement_forms import form_def, form_defs

    kinds = next(f for f in form_def(_NEW)["fields"] if f["key"] == "agreement_type")["options"]
    have = {(u["form"], u["agreement_type"] or "") for f in _FLOWS for u in f.criteria["used_for"]}
    missing = [k for k in kinds if (_NEW, k) not in have]
    missing += [f["key"] for f in form_defs() if f["key"] not in (_NEW, "cancellation") and (f["key"], "") not in have]
    assert missing == []
