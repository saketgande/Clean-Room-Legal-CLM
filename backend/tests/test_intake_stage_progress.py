"""The Legal Intake stage column follows the workflow, not a status nobody moves.

It was a request-status spine (new → assigned → review → complete) that no step
advanced once the workflow engine took over, so every request read "New · 0/4"
from filing to signature.
"""

from types import SimpleNamespace

from app.intake.service import _workflow

STEPS = [{"name": "Prepare draft", "stage": "drafting"}, {"name": "Legal review", "stage": "review"},
         {"name": "Finance approval", "stage": "approval"}, {"name": "Signature", "stage": "signature"}]


def _run(index, status="waiting"):
    return SimpleNamespace(steps=STEPS, current_index=index, status=status)


def test_the_current_step_s_stage_is_active_and_earlier_ones_done():
    out = _workflow(None, _run(2))
    assert [s["stage"] for s in out] == ["intake", "drafting", "review", "approval", "signature"]
    assert [s["done"] for s in out] == [True, True, True, False, False]
    assert out[3]["active"] and out[3]["label"] == "Approval · Finance approval"


def test_a_finished_run_is_all_done_and_no_run_shows_nothing():
    assert all(s["done"] and not s["active"] for s in _workflow(None, _run(4, "complete")))
    assert _workflow(None, None) == []
