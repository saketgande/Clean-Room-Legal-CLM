"""Workflow Router agent — reads an intake request and suggests which governance
workflow (Workflow) it should ride, grounded on the org's live flow catalog.

Suggest-only itself: it never starts a flow — that's
app.intake.service._maybe_autostart_workflow's call, gated on this module's
own confidence score. A reviewer can also accept the suggestion manually on
the ticket (one click → flows/start). Two layers by design:

  * deterministic baseline — `select_flow`'s criteria match, always available
    and used verbatim when mocks are on or the model call fails, so there is
    always a real suggestion and something to A/B the model against.
  * Claude — the model layer, grounded against the same catalog (including
    each flow's free-text `criteria.ai_condition`, if set) and never trusted
    blindly: a returned flow id that isn't one of the real candidates is
    discarded rather than acted on.

Litigation-specific extraction is a later specialization layered on top of this
generic router (see the plan); this module ships the generic capability.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.intake.models import IntakeRequest

logger = logging.getLogger(__name__)

# Below this the suggestion is shown but flagged "needs a human's eye".
_CONFIDENT = 0.6


def flow_catalog(db: Session, org_id: str) -> list[dict]:
    """The enabled flows this org can route to — the model's only menu."""
    from sqlalchemy import select

    from app.workflows.models import Workflow

    flows = db.scalars(
        select(Workflow).where(Workflow.org_id == org_id, Workflow.enabled.is_(True)).order_by(Workflow.eval_order.asc())
    ).all()
    out = []
    for f in flows:
        steps = [s.get("name") or s.get("type") for s in (f.steps or [])]
        out.append({
            "id": f.id, "name": f.name, "description": f.description or "",
            "criteria": f.criteria or {}, "steps": [s for s in steps if s],
            "is_catch_all": not (f.criteria or {}),
        })
    return out


def _baseline(db: Session, request: IntakeRequest, catalog: list[dict]) -> dict:
    """Deterministic suggestion from the existing criteria matcher."""
    from app.workflows.service import select_flow

    flow = select_flow(db, request=request)
    if flow is None:
        return {"flow_id": None, "flow_name": None, "confidence": 0.0,
                "reasoning": "No workflow criteria matched this request.",
                "alternatives": [], "needs_human": True, "source": "deterministic", "steps": []}
    entry = next((c for c in catalog if c["id"] == flow.id), None)
    catch_all = entry["is_catch_all"] if entry else not (flow.criteria or {})
    conf = 0.45 if catch_all else 0.72
    why = ("Default ladder — no specialised workflow matched."
           if catch_all else f"Matched by rule {flow.criteria}.")
    return {"flow_id": flow.id, "flow_name": flow.name, "confidence": conf,
            "reasoning": why, "alternatives": [], "needs_human": conf < _CONFIDENT,
            "source": "deterministic", "steps": entry["steps"] if entry else []}


_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": ["string", "null"],
                    "description": "id of the single best-fit flow from the catalog, or null if none fits"},
        "confidence": {"type": "number", "description": "0..1 confidence in the pick"},
        "reasoning": {"type": "string", "description": "one or two sentences, plain English"},
        "alternatives": {"type": "array", "items": {
            "type": "object",
            "properties": {"flow_id": {"type": "string"}, "why": {"type": "string"}},
            "required": ["flow_id", "why"]}},
        "needs_human": {"type": "boolean"},
    },
    "required": ["flow_id", "confidence", "reasoning", "needs_human"],
}


def _prompt_for(request: IntakeRequest, catalog: list[dict]) -> str:
    lines = ["WORKFLOW CATALOG (choose exactly one id, or null):"]
    for c in catalog:
        crit = "any request" if c["is_catch_all"] else str(c["criteria"])
        condition = (c["criteria"] or {}).get("ai_condition")
        steps = " → ".join(c["steps"][:4]) or "—"
        entry = f"- id={c['id']} · {c['name']}\n    when: {crit}\n    steps: {steps}\n    {c['description']}"
        if condition:
            entry += f"\n    AI condition (weigh this heavily): {condition}"
        lines.append(entry)
    fv = request.field_values or {}
    field_txt = "\n".join(f"  {k}: {v}" for k, v in fv.items() if not str(k).startswith("_")) or "  (none)"
    lines += [
        "\nINTAKE REQUEST:",
        f"  Type: {request.type_label}",
        f"  Priority: {request.priority}",
        f"  Description: {(request.description or '')[:3000]}",
        f"  Structured fields:\n{field_txt}",
    ]
    return "\n".join(lines)


def suggest_flow(db: Session, request: IntakeRequest, *, claude_client=None) -> dict:
    """Best-fit flow for a request. Always returns a dict (never raises)."""
    from app.core.config import settings

    catalog = flow_catalog(db, request.org_id)
    if not catalog:
        return {"flow_id": None, "flow_name": None, "confidence": 0.0,
                "reasoning": "No workflows are configured yet.", "alternatives": [],
                "needs_human": True, "source": "deterministic", "steps": []}

    baseline = _baseline(db, request, catalog)

    if settings.mock_claude:
        return baseline

    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import run_coro_blocking
    from app.integrations.dependencies import get_claude_client

    claude_client = claude_client or get_claude_client()
    ids = {c["id"] for c in catalog}
    names = {c["id"]: c["name"] for c in catalog}
    steps_by_id = {c["id"]: c["steps"] for c in catalog}
    bundle = get_agent_prompt(db, agent_id="flow_router", org_id=request.org_id)
    user_prompt = _prompt_for(request, catalog)
    try:
        enforce_daily_token_cap(request.org_id)
        resp = run_coro_blocking(lambda: claude_client.complete_structured(
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=user_prompt,
            tool_name="suggest_flow", input_schema=_SCHEMA,
            max_tokens=600, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(db, org_id=request.org_id, agent_id="flow_router", prompt_bundle=bundle,
                        input_payload={"user_prompt": user_prompt}, response=resp)
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = (blocks[0].get("input") if blocks else None)
        if not isinstance(data, dict):
            return {**baseline, "source": "degraded"}

        fid = data.get("flow_id")
        if fid not in ids:  # ungrounded / null → hand to a human, keep baseline pick visible
            fid = None
        conf = data.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) else baseline["confidence"]
        conf = max(0.0, min(1.0, conf))
        alts = [{"flow_id": a["flow_id"], "flow_name": names.get(a["flow_id"], ""), "why": a.get("why", "")}
                for a in (data.get("alternatives") or [])
                if isinstance(a, dict) and a.get("flow_id") in ids and a.get("flow_id") != fid]
        return {
            "flow_id": fid,
            "flow_name": names.get(fid) if fid else None,
            "confidence": conf,
            "reasoning": str(data.get("reasoning") or baseline["reasoning"])[:600],
            "alternatives": alts[:3],
            "needs_human": bool(data.get("needs_human")) or fid is None or conf < _CONFIDENT,
            "source": "llm",
            "steps": steps_by_id.get(fid, []),
        }
    except Exception:
        logger.warning("flow-router model call failed for %s", request.id, exc_info=True)
        return {**baseline, "source": "degraded"}
