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
    hold = any(w in desc for w in ("hold", "preserve", "spoliation", "litigation hold"))
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
        "legal_hold_required": hold or True,   # litigation almost always needs a hold
        "outside_counsel_likely": True,        # default to yes for a real dispute
        "settlement_posture": "none",
        "key_parties": parties,
        "summary": (request.description or "").split("\n")[0][:280],
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
        "flow_id": {"type": ["string", "null"], "description": "best-fit flow id from the catalog"},
        "flow_confidence": {"type": "number"},
        "flow_reasoning": {"type": "string"},
    },
    "required": ["matter_type", "legal_hold_required", "outside_counsel_likely",
                 "settlement_posture", "summary", "flow_id", "flow_confidence", "flow_reasoning"],
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

    import asyncio

    from app.integrations.claude import ClaudeClient

    ids = {c["id"] for c in catalog}
    names = {c["id"]: c["name"] for c in catalog}
    try:
        resp = asyncio.run(ClaudeClient().complete_structured(
            system_prompt=(
                "You are the Litigation Intake Agent for an in-house legal team. From the request, "
                "extract: the matter type; every statutory or response deadline with the source of each; "
                "whether a legal hold is required; whether outside counsel is likely needed; the settlement "
                "posture (none/proposed/likely); and the key parties. Then pick the single best-fit "
                "governance workflow from the catalog — strongly prefer litigation, dispute, notice, "
                "regulatory, investigation, or employment workflows over generic contract ladders. Only "
                "return a flow_id that appears in the catalog. Be concise; never invent facts or workflows."),
            user_prompt=_prompt(request, catalog),
            tool_name="assess_litigation", input_schema=_SCHEMA,
            max_tokens=900, temperature=0.0,
        ))
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = (blocks[0].get("input") if blocks else None)
        if not isinstance(data, dict):
            return {**baseline, "source": "degraded"}

        fid = data.get("flow_id")
        if fid not in ids:
            fid = None
        conf = data.get("flow_confidence")
        conf = max(0.0, min(1.0, float(conf))) if isinstance(conf, (int, float)) else baseline["flow_suggestion"]["confidence"]
        parties = [str(p) for p in (data.get("key_parties") or []) if p] or baseline["key_parties"]
        return {
            "matter_type": str(data.get("matter_type") or baseline["matter_type"])[:120],
            "statutory_deadlines": [d for d in (data.get("statutory_deadlines") or []) if isinstance(d, dict) and d.get("what")][:6],
            "legal_hold_required": bool(data.get("legal_hold_required")),
            "outside_counsel_likely": bool(data.get("outside_counsel_likely")),
            "settlement_posture": str(data.get("settlement_posture") or "none").lower(),
            "key_parties": parties[:8],
            "summary": str(data.get("summary") or baseline["summary"])[:400],
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
