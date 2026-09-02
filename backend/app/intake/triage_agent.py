"""Context-aware intake triage — the decider.

Reads the WHOLE request (type, subject, description, every structured field) and
produces one structured understanding that drives classification, complexity,
risk, urgency and the workflow pick. This replaces the keyword classifier
(`agents.classify`) + keyword flow pick (`select_flow`) as the thing that
DECIDES; those stay as the deterministic fallback for when the model is mocked,
capped, errors, or returns nothing groundable.

The returned dict is shape-compatible with `agents.classify` (same
category/agent_id/complexity/risk_flag/confidence/source keys) so the Tier-0
gates, the workflow `ai_task` steps and the frontend keep working unchanged — it
just fills those keys from real understanding (`source="llm"`) and adds an
`understanding` block plus the `flow_suggestion`.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.intake.models import IntakeRequest

logger = logging.getLogger(__name__)

# Categories the rest of the system already understands (agents._CATEGORY_METADATA).
_CATEGORIES = ["NDA", "Litigation", "Privacy", "Trademark", "Vendor", "Contract Review", "Policy/FAQ", "General"]
_COMPLEXITY = ["simple", "standard", "complex"]
_RISK = ["low", "medium", "high"]
_URGENCY = ["Low", "Medium", "High"]
# Below this the pick is shown but flagged for a human's eye.
_CONFIDENT = 0.6


_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": _CATEGORIES,
                     "description": "the single best-fit matter type, judged by meaning not keywords"},
        "sub_type": {"type": ["string", "null"],
                     "description": "the specific flavour, e.g. 'custom NDA (no template)', 'standard mutual NDA', 'MSA', 'DPA'"},
        "complexity": {"type": "string", "enum": _COMPLEXITY,
                       "description": "judge from the ACTUAL request — deal value, bespoke terms, multi-party, cross-border all raise it; do not default by matter type"},
        "risk": {"type": "string", "enum": _RISK},
        "urgency": {"type": "string", "enum": _URGENCY,
                    "description": "how time-critical the requester makes it sound"},
        "business_unit": {"type": ["string", "null"], "description": "the requesting business unit / department if stated or inferable"},
        "estimated_value": {"type": ["number", "null"], "description": "the deal/contract value in USD if stated or reasonably inferable, else null"},
        "jurisdiction": {"type": ["string", "null"], "description": "governing law / country if stated"},
        "key_asks": {"type": "array", "items": {"type": "string"},
                     "description": "what the requester actually wants, in plain terms (e.g. 'draft a custom NDA, do not use the standard template')"},
        "missing_info": {"type": "array", "items": {"type": "string"},
                         "description": "CRITICAL facts this matter type needs to be drafted or handled properly that the request does NOT provide — e.g. 'contract value', 'term length', 'governing law', 'purpose of disclosure', 'counterparty legal name'. Empty if the essentials are all present. List only truly needed items, not nice-to-haves."},
        "recommended_workflow_id": {"type": ["string", "null"],
                                    "description": "id of the single best-fit workflow from the catalog, or null if none fits or a human should route it"},
        "workflow_reasoning": {"type": "string", "description": "one or two sentences on the workflow pick"},
        "confidence": {"type": "number", "description": "0..1 — honest confidence in this whole read"},
        "reasoning": {"type": "string", "description": "one or two sentences summarising your understanding of the request"},
        "needs_human": {"type": "boolean", "description": "true if ambiguous or high-stakes enough that a human should confirm the routing"},
    },
    "required": ["category", "complexity", "risk", "urgency", "confidence", "reasoning", "needs_human"],
}


def _prompt(request: IntakeRequest, catalog: list[dict]) -> str:
    lines = ["WORKFLOW CATALOG (choose exactly one id for recommended_workflow_id, or null):"]
    for c in catalog:
        crit = "any request" if c.get("is_catch_all") else str(c.get("criteria"))
        steps = " → ".join((c.get("steps") or [])[:4]) or "—"
        lines.append(f"- id={c['id']} · {c['name']}\n    when: {crit}\n    steps: {steps}\n    {c.get('description') or ''}")
    fv = request.field_values or {}
    field_txt = "\n".join(f"  {k}: {v}" for k, v in fv.items() if not str(k).startswith("_")) or "  (none)"
    lines += [
        "\nINTAKE REQUEST:",
        f"  Type: {request.type_label}",
        f"  Subject: {request.subject or ''}",
        f"  Stated priority: {request.priority}",
        f"  Description: {(request.description or '')[:3000]}",
        f"  Structured fields:\n{field_txt}",
    ]
    return "\n".join(lines)


def _fallback(db: Session, request: IntakeRequest, *, degraded: bool = False) -> dict:
    """Deterministic path — the existing regex classifier + keyword flow pick.

    When ``degraded`` (the AI call failed, vs. mock mode), mark it so the request
    surfaces "AI triage unavailable — auto-classified by keyword, review this"
    AND forces human review, so a keyword guess never silently auto-routes."""
    from app.intake import agents
    from app.intake.flow_agent import suggest_flow

    base = agents.classify(request.type_label, request.description or "")
    base["understanding"] = None
    base["flow_suggestion"] = suggest_flow(db, request)
    if degraded:
        base["degraded"] = True
        base["degraded_reason"] = (
            "AI triage was unavailable — this request was auto-classified by keyword "
            "rules, not a model reading. Please confirm the category and routing."
        )
        base["needs_human"] = True
        if isinstance(base.get("flow_suggestion"), dict):
            base["flow_suggestion"]["needs_human"] = True
    return base


def triage(db: Session, request: IntakeRequest) -> dict:
    """Best-effort context-aware triage. Always returns a dict (never raises)."""
    from app.core.config import settings
    from app.intake.flow_agent import flow_catalog

    catalog = flow_catalog(db, request.org_id)
    if settings.mock_claude:
        return _fallback(db, request)

    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import ClaudeClient, run_coro_blocking

    try:
        enforce_daily_token_cap(request.org_id)
        bundle = get_agent_prompt(db, agent_id="intake_triage", org_id=request.org_id)
        user_prompt = _prompt(request, catalog)
        resp = run_coro_blocking(lambda: ClaudeClient().complete_structured(
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=user_prompt,
            tool_name="triage_request", input_schema=_SCHEMA,
            max_tokens=900, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(db, org_id=request.org_id, agent_id="intake_triage", prompt_bundle=bundle,
                       input_payload={"user_prompt": user_prompt}, response=resp)
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = blocks[0].get("input") if blocks else None
        if not isinstance(data, dict):
            return _fallback(db, request, degraded=True)
        return _to_triage(request, data, catalog)
    except Exception:
        logger.warning("intake triage model call failed for %s", request.id, exc_info=True)
        return _fallback(db, request, degraded=True)


def _to_triage(request: IntakeRequest, data: dict, catalog: list[dict]) -> dict:
    """Map the model's structured read onto the ai_triage shape the rest of the
    system reads, overriding the category's DEFAULT complexity/risk with the
    model's actual assessment of THIS request (the fix for '$50M custom NDA =
    simple')."""
    from app.intake import agents

    category = data.get("category") if data.get("category") in _CATEGORIES else "General"
    conf = data.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else 0.0
    conf = max(0.0, min(1.0, conf))

    base = agents.result_for_category(category, round(conf, 2), source="llm")
    cx = data.get("complexity")
    if cx in _COMPLEXITY:
        base["complexity"] = cx
    rk = data.get("risk")
    if rk in _RISK:
        base["risk_flag"] = rk

    ids = {c["id"] for c in catalog}
    names = {c["id"]: c["name"] for c in catalog}
    fid = data.get("recommended_workflow_id")
    if fid not in ids:  # ungrounded / null → hand to a human
        fid = None
    needs_human = bool(data.get("needs_human")) or fid is None or conf < _CONFIDENT
    urgency = data.get("urgency") if data.get("urgency") in _URGENCY else None

    missing = [str(m) for m in (data.get("missing_info") or []) if str(m).strip()][:8]
    base["understanding"] = {
        "sub_type": data.get("sub_type"),
        "urgency": urgency,
        "business_unit": data.get("business_unit"),
        "estimated_value": data.get("estimated_value"),
        "jurisdiction": data.get("jurisdiction"),
        "key_asks": data.get("key_asks") or [],
        "missing_info": missing,
        "reasoning": str(data.get("reasoning") or "")[:800],
    }
    # Completeness gate: critical info missing → flag the request and hold
    # automation (no silent auto-draft with playbook defaults).
    base["needs_info"] = bool(missing)
    base["flow_suggestion"] = {
        "flow_id": fid,
        "flow_name": names.get(fid) if fid else None,
        "confidence": conf,
        "reasoning": str(data.get("workflow_reasoning") or data.get("reasoning") or "")[:600],
        "alternatives": [],
        "needs_human": needs_human,
        "source": "llm",
    }
    return base
