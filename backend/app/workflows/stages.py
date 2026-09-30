"""Lifecycle stages for workflow steps.

Every contract goes through the same seven stages; a workflow's steps sit
under them (``step["stage"]``), so the lifecycle view can show, say, every
review step under Review whatever the workflow calls them. Values match
``ContractLifecycleStage``.

Rules: a draft step is always Drafting, an approval step Approval, a signature
step Signature; a counterparty negotiation is Drafting or Review; other steps
may sit anywhere. Steps follow the lifecycle order, and steps running in
parallel share a stage.
"""

from __future__ import annotations

STAGES = ("intake", "drafting", "review", "approval", "signature", "active", "closed")
_RANK = {s: i for i, s in enumerate(STAGES)}
FIXED = {"clm_draft": "drafting", "approval": "approval", "signature": "signature"}
ALLOWED = {**{t: (s,) for t, s in FIXED.items()}, "counterparty": ("drafting", "review")}


def allowed(step_type: str) -> tuple[str, ...]:
    return ALLOWED.get(step_type, STAGES)


def infer(steps: list[dict]) -> list[dict]:
    """Fill in a missing or impossible ``stage`` on each step, in place. A draft,
    approval or signature step gets its fixed stage; any other step goes to
    Review — or Intake when a draft step still follows it, Active once the
    contract has been signed — and never earlier than the step before. Steps
    that already carry a valid stage keep it."""
    fixed_after = [None] * len(steps)  # rank of the next fixed-stage step after i
    nxt = None
    for i in range(len(steps) - 1, -1, -1):
        fixed_after[i] = nxt
        if steps[i].get("type") in FIXED:
            nxt = _RANK[FIXED[steps[i]["type"]]]
    floor = 0
    for i, st in enumerate(steps):
        t = st.get("type")
        stage = st.get("stage")
        if stage not in allowed(t):
            if t in FIXED:
                stage = FIXED[t]
            elif st.get("parallel") and i > 0:
                stage = steps[i - 1]["stage"]
            else:
                cap = fixed_after[i]
                want = _RANK["intake"] if cap is not None and cap < _RANK["review"] else _RANK["review"]
                stage = STAGES[max(floor, want)]
                if stage not in allowed(t):
                    stage = allowed(t)[-1]
            st["stage"] = stage
        floor = max(floor, _RANK[stage])
        if t == "signature":
            floor = max(floor, _RANK["active"])  # later steps run on the signed contract
    return steps


def problems(steps: list[dict]) -> list[str]:
    """Why these steps break the stage rules, in plain words (empty: fine)."""
    out: list[str] = []
    prev = None
    for i, st in enumerate(steps):
        stage, name = st.get("stage"), st.get("name") or "a step"
        if stage not in STAGES:
            out.append(f"“{name}” has no lifecycle stage.")
            continue
        if stage not in allowed(st.get("type")):
            out.append(f"“{name}” can only be in {' or '.join(s.title() for s in allowed(st.get('type')))}.")
        if prev is not None:
            if _RANK[stage] < _RANK[prev["stage"]]:
                out.append(f"“{name}” is in {stage.title()} but comes after “{prev.get('name')}” in "
                           f"{prev['stage'].title()} — steps must follow the lifecycle order.")
            elif st.get("parallel") and i > 0 and stage != prev["stage"]:
                out.append(f"“{name}” runs in parallel with “{prev.get('name')}”, so it must be in the same stage.")
        prev = st
    return out


if __name__ == "__main__":  # pragma: no cover - rule self-check
    s = infer([{"type": "human_task", "name": "Intake"}, {"type": "clm_draft"}, {"type": "ai_task"},
               {"type": "approval"}, {"type": "human_task"}, {"type": "signature"}, {"type": "ai_task"}])
    assert [x["stage"] for x in s] == ["intake", "drafting", "review", "approval", "approval", "signature", "active"], s
    assert problems(s) == [], problems(s)
    bad = [{"type": "approval", "name": "GC", "stage": "approval"}, {"type": "human_task", "name": "Legal", "stage": "review"}]
    assert "follow the lifecycle order" in problems(bad)[0]
    ok = infer([{"type": "clm_draft"}, {"type": "human_task"}, {"type": "approval"}, {"type": "signature"}])
    assert problems(ok) == [], problems(ok)
    print("stage rules self-check passed")
