"""Every workflow step sits under a lifecycle stage, in lifecycle order.

Contracts all follow Intake → Drafting → Review → Approval → Signature →
Active → Closed, and the lifecycle view lists a workflow's steps under those
stages. A step with no stage, or a Review step after an Approval, would show
up in the wrong place — or the contract would go backwards.
"""

import pytest
from fastapi import HTTPException

from app.workflows import stages
from app.workflows.builtin import BUILTIN_FLOWS
from app.workflows.service import _clean_steps


def test_a_saved_workflow_gets_stages_filled_in():
    out = _clean_steps([{"type": "clm_draft", "name": "Draft"}, {"type": "human_task", "name": "Legal review"},
                        {"type": "approval", "name": "GC"}, {"type": "signature", "name": "Sign"},
                        {"type": "ai_task", "name": "Obligations"}])
    assert [s["stage"] for s in out] == ["drafting", "review", "approval", "signature", "active"]


def test_a_review_after_an_approval_is_refused_in_plain_words():
    with pytest.raises(HTTPException) as exc:
        _clean_steps([{"type": "approval", "name": "GC"},
                      {"type": "human_task", "name": "Legal review", "stage": "review"}])
    assert exc.value.status_code == 422 and "lifecycle order" in exc.value.detail


def test_an_approval_step_cannot_be_moved_out_of_approval():
    """Its type fixes its stage; the chosen stage is corrected, not trusted."""
    [step] = _clean_steps([{"type": "approval", "name": "GC", "stage": "review"}])
    assert step["stage"] == "approval"


def test_parallel_steps_share_a_stage():
    with pytest.raises(HTTPException) as exc:
        _clean_steps([{"type": "human_task", "name": "Legal", "stage": "review"},
                      {"type": "human_task", "name": "Obligations", "stage": "active", "parallel": True}])
    assert "same stage" in exc.value.detail


def test_every_built_in_workflow_follows_the_lifecycle():
    for flow in BUILTIN_FLOWS:
        assert stages.problems(flow["steps"]) == [], flow["name"]
        assert all(s.get("stage") in stages.STAGES for s in flow["steps"]), flow["name"]
