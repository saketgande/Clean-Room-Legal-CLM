"""Litigation Intake Agent — the specialization layered on the generic flow
router (see flow_agent.py). For litigation-category requests it extracts the
matter facts a triaging lawyer needs (matter type, statutory deadlines, whether
a legal hold is required, outside-counsel likelihood, settlement posture, key
parties) AND makes a confident, litigation-biased flow pick instead of the
generic router's "hand it to a human".

Its extraction also pre-fills the litigation flow's branch fields
(handled_inhouse / settlement_proposed) so the ladder's conditional steps route
correctly (Outside Counsel Engagement, Settlement Antitrust Review).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.intake.flow_agent import _CONFIDENT, flow_catalog
from app.intake.models import IntakeRequest

logger = logging.getLogger(__name__)


def is_litigation(request: IntakeRequest) -> bool:
    cat = (request.ai_triage or {}).get("category")
    return str(cat or "").strip().lower() == "litigation"


def branch_fields(assessment: dict) -> dict:
    """Derived answers the litigation flow's conditional steps key off."""
    return {
        "handled_inhouse": not bool(assessment.get("outside_counsel_likely")),
        "settlement_proposed": str(assessment.get("settlement_posture") or "none").lower() in ("proposed", "likely"),
    }


def _heuristic(db: Session, request: IntakeRequest, catalog: list[dict]) -> dict:
    """Deterministic assessment — used under mocks or when the model fails."""
    from app.flows.service import select_flow

    desc = (request.description or "").lower()
    matter = ("Legal notice / demand" if "notice" in desc or "demand" in desc
              else "Patent / ANDA (Para IV)" if "ande" in desc or "anda" in desc or "para iv" in desc or "patent" in desc
              else "Employment matter" if "employ" in desc or "posh" in desc
              else "Regulatory action" if "regulat" in desc or "483" in desc
              else "Litigation / dispute")
    parties = [p.get("name") for p in (request.parties or []) if isinstance(p, dict) and p.get("name")]

    flow = select_flow(db, request=request)
    entry = next((c for c in catalog if flow and c["id"] == flow.id), None)
    if flow:
        catch_all = entry["is_catch_all"] if entry else not (flow.criteria or {})
        conf = 0.5 if catch_all else 0.7
        fs = {"flow_id": flow.id, "flow_name": flow.name, "confidence": conf,
              "reasoning": f"Litigation matter matched to {flow.name}.",
              "alternatives": [], "needs_human": conf < _CONFIDENT, "source": "deterministic"}
    else:
        fs = {"flow_id": None, "flow_name": None, "confidence": 0.0,
              "reasoning": "No litigation workflow matched.", "alternatives": [],
              "needs_human": True, "source": "deterministic"}
    return {
        "matter_type": matter,
        "statutory_deadlines": [],
        # Conservative default while the real model is unavailable: a missed
        # legal hold is a genuine spoliation/sanctions risk, an unneeded one is
        # just a badge a lawyer dismisses — always flag it here rather than gate
        # on a handful of keywords that could easily be absent from the text.
        "legal_hold_required": True,
        "outside_counsel_likely": True,        # default to yes for a real dispute
        "settlement_posture": "none",
        "key_parties": parties,
        "summary": (request.description or "").split("\n")[0][:280],
        # A keyword guess, never a real analysis — fixed, honest, low value
        # regardless of how confident the flow pick above happens to be.
        "assessment_confidence": 0.3,
        "flow_suggestion": fs,
    }


_SCHEMA = {
    "type": "object",
    "properties": {
        "matter_type": {"type": "string"},
        "statutory_deadlines": {"type": "array", "items": {
            "type": "object",
            "properties": {"what": {"type": "string"}, "date": {"type": "string"}, "source": {"type": "string"}},
            "required": ["what"]}},
        "legal_hold_required": {"type": "boolean"},
        "outside_counsel_likely": {"type": "boolean"},
        "settlement_posture": {"type": "string", "enum": ["none", "proposed", "likely"]},
        "key_parties": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "assessment_confidence": {
            "type": "number",
            "description": "0-1: confidence in the EXTRACTED FACTS above (matter type, deadlines, "
                           "hold/counsel calls), by how much concrete detail the request gives — "
                           "distinct from flow_confidence, which is only about the workflow pick.",
        },
        "flow_id": {"type": ["string", "null"], "description": "best-fit flow id from the catalog"},
        "flow_confidence": {"type": "number"},
        "flow_reasoning": {"type": "string"},
    },
    "required": ["matter_type", "legal_hold_required", "outside_counsel_likely",
                 "settlement_posture", "summary", "assessment_confidence",
                 "flow_id", "flow_confidence", "flow_reasoning"],
}


def _prompt(request: IntakeRequest, catalog: list[dict]) -> str:
    lines = ["WORKFLOW CATALOG (choose exactly one id, or null):"]
    for c in catalog:
        crit = "any request" if c["is_catch_all"] else str(c["criteria"])
        lines.append(f"- id={c['id']} · {c['name']} · when: {crit} · {c['description']}")
    lines += ["\nLITIGATION REQUEST:",
              f"  Type: {request.type_label}",
              f"  Priority: {request.priority}",
              f"  Description: {(request.description or '')[:3500]}"]
    return "\n".join(lines)


def assess_litigation(db: Session, request: IntakeRequest) -> dict:
    """Full litigation assessment + a confident flow pick. Never raises."""
    from app.core.config import settings

    catalog = flow_catalog(db, request.org_id)
    baseline = _heuristic(db, request, catalog)
    if settings.mock_claude or not catalog:
        return baseline

    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import ClaudeClient, run_coro_blocking

    ids = {c["id"] for c in catalog}
    names = {c["id"]: c["name"] for c in catalog}
    bundle = get_agent_prompt(db, agent_id="litigation_intake_agent", org_id=request.org_id)
    user_prompt = _prompt(request, catalog)
    try:
        enforce_daily_token_cap(request.org_id)
        resp = run_coro_blocking(lambda: ClaudeClient().complete_structured(
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=user_prompt,
            tool_name="assess_litigation", input_schema=_SCHEMA,
            max_tokens=900, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(db, org_id=request.org_id, agent_id="litigation_intake_agent", prompt_bundle=bundle,
                        input_payload={"user_prompt": user_prompt}, response=resp)
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = (blocks[0].get("input") if blocks else None)
        if not isinstance(data, dict):
            return {**baseline, "source": "degraded"}

        fid = data.get("flow_id")
        if fid not in ids:
            fid = None
        conf = data.get("flow_confidence")
        conf = max(0.0, min(1.0, float(conf))) if isinstance(conf, (int, float)) else baseline["flow_suggestion"]["confidence"]
        ac = data.get("assessment_confidence")
        assessment_confidence = (
            max(0.0, min(1.0, float(ac))) if isinstance(ac, (int, float)) else baseline["assessment_confidence"]
        )
        parties = [str(p) for p in (data.get("key_parties") or []) if p] or baseline["key_parties"]
        return {
            "matter_type": str(data.get("matter_type") or baseline["matter_type"])[:120],
            "statutory_deadlines": [d for d in (data.get("statutory_deadlines") or []) if isinstance(d, dict) and d.get("what")][:6],
            "legal_hold_required": bool(data.get("legal_hold_required")),
            "outside_counsel_likely": bool(data.get("outside_counsel_likely")),
            "settlement_posture": str(data.get("settlement_posture") or "none").lower(),
            "key_parties": parties[:8],
            "summary": str(data.get("summary") or baseline["summary"])[:400],
            "assessment_confidence": assessment_confidence,
            "flow_suggestion": {
                "flow_id": fid,
                "flow_name": names.get(fid) if fid else None,
                "confidence": conf,
                "reasoning": str(data.get("flow_reasoning") or baseline["flow_suggestion"]["reasoning"])[:600],
                "alternatives": [],
                "needs_human": fid is None or conf < _CONFIDENT,
                "source": "llm",
            },
        }
    except Exception:
        logger.warning("litigation-agent model call failed for %s", request.id, exc_info=True)
        return {**baseline, "source": "degraded"}
