"""Context-aware intake triage — the decider.

Reads the WHOLE request (type, subject, description, every structured field) and
produces one structured understanding that drives classification, complexity,
risk, urgency and the workflow pick. This replaces the keyword classifier
(`agents.classify`) + keyword flow pick (`select_flow`) as the thing that
DECIDES; those stay as the deterministic fallback for when the model is mocked,
capped, errors, or returns nothing groundable.

The returned dict is shape-compatible with `agents.classify` (same
category/agent_id/complexity/risk_flag/confidence/source keys) so owner
assignment, the workflow `ai_task` steps and the frontend keep working unchanged — it
just fills those keys from real understanding (`source="llm"`) and adds an
`understanding` block plus the `flow_suggestion`.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intake.models import IntakeDocument, IntakeRequest

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
                     "description": "the single best-fit matter type, judged by meaning not keywords. NDA: confidentiality agreements. Vendor: buying goods/services from a supplier on our paper. Contract Review: any other agreement to draft or review (MSA, SoW, amendment, renewal, counterparty paper). Privacy: DPAs, data incidents, privacy questions. Litigation: disputes, claims, notices of breach, court papers. Trademark: marks and brand. Policy/FAQ: a question about a policy or how to do something. General: anything else"},
        "sub_type": {"type": ["string", "null"],
                     "description": "the specific flavour, e.g. 'custom NDA (no template)', 'standard mutual NDA', 'MSA', 'DPA'"},
        "complexity": {"type": "string", "enum": _COMPLEXITY,
                       "description": "judge from the ACTUAL request — deal value, bespoke terms, multi-party, cross-border all raise it; do not default by matter type"},
        "risk": {"type": "string", "enum": _RISK},
        "urgency": {"type": "string", "enum": _URGENCY,
                    "description": "how time-critical the requester makes it sound"},
        "business_unit": {"type": ["string", "null"], "description": "the requesting business unit / department if stated or inferable"},
        "estimated_value": {"type": ["number", "null"], "description": "the deal/contract value as stated (in its own currency, do not convert), else null"},
        "jurisdiction": {"type": ["string", "null"], "description": "governing law / country if stated"},
        "key_asks": {"type": "array", "items": {"type": "string"},
                     "description": "what the requester actually wants, in plain terms (e.g. 'draft a custom NDA, do not use the standard template')"},
        "missing_info": {"type": "array", "items": {"type": "string"},
                         "description": "CRITICAL facts this matter type needs to be drafted or handled properly that the request does NOT provide — e.g. 'counterparty legal name', 'what the agreement is for'. Only facts WITHOUT which no one could start the work; anything negotiable or with a standard default (term, governing law, value) is NOT missing. Usually empty."},
        "recommended_workflow_id": {"type": ["string", "null"],
                                    "description": "id of the single best-fit workflow from the catalog, or null if none fits or a human should route it"},
        "workflow_reasoning": {"type": "string", "description": "one or two sentences on the workflow pick"},
        "confidence": {"type": "number", "description": "0..1 — honest confidence in this whole read"},
        "reasoning": {"type": "string", "description": "one or two sentences summarising your understanding of the request"},
        "needs_human": {"type": "boolean", "description": "true if ambiguous or high-stakes enough that a human should confirm the routing"},
    },
    "required": ["category", "complexity", "risk", "urgency", "confidence", "reasoning", "needs_human"],
}


_ATTACHMENT_EXCERPT_CHARS = 1500
_ATTACHMENTS_IN_PROMPT = 3


def _prompt(request: IntakeRequest, catalog: list[dict], attachments: list | tuple = ()) -> str:
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
    # Attachments are filed with the request, so triage reads them too — capped,
    # because they are context for routing, not the document review itself.
    docs = [d for d in attachments if d.extracted_text][:_ATTACHMENTS_IN_PROMPT]
    if docs:
        lines.append("  Attachments (excerpts):")
        for d in docs:
            lines.append(f"  --- {d.filename} ---\n{d.extracted_text[:_ATTACHMENT_EXCERPT_CHARS]}")
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


def triage(db: Session, request: IntakeRequest, *, claude_client=None) -> dict:
    """Best-effort context-aware triage. Always returns a dict (never raises).

    An agreement-form request is decided by its form (``form_triage``); only
    free text (email, Teams, chat, notices) is triaged by the model.

    An admin's "Used for" workflow beats whatever the model or the fallback
    picked: it is a decision, not a guess, and autostart reads this result."""
    if form_key(request):
        return form_triage(db, request, claude_client=claude_client)
    result = _triage(db, request, claude_client=claude_client)
    try:
        from app.intake.flow_agent import used_for_suggestion

        set_up = used_for_suggestion(db, request)
    except Exception:
        logger.warning("used-for lookup failed for %s", request.id, exc_info=True)
        set_up = None
    # A request without a form can only be "Used for"-matched by its type. That pin
    # yields to litigation: request.ai_triage isn't saved yet, so the fresh
    # category is checked here (afterwards, _used_for_rank checks the saved one).
    if set_up and str(result.get("category") or "").strip().lower() == "litigation":
        set_up = None
    if set_up:
        result["flow_suggestion"] = set_up
    return result


def form_key(request: IntakeRequest) -> str | None:
    """The agreement-wizard form this request was filed through, if any."""
    from app.intake.agreement_forms import form_defs

    key = (request.field_values or {}).get("request_form")
    return key if key in {f["key"] for f in form_defs()} else None


def _form_category(key: str, fv: dict) -> str:
    """The matter type a form states. Picks the owning team by expertise."""
    if key == "dpa":
        return "Privacy"
    kind = fv.get("agreement_type") if key == "new_agreement" else None
    return {"NDA": "NDA", "Buying from a vendor": "Vendor", "Software or SaaS": "Vendor"}.get(kind, "Contract Review")


def form_triage(db: Session, request: IntakeRequest, *, claude_client=None) -> dict:
    """Triage for an agreement-form request: the form already states the type,
    value, dates and parties, so nothing is guessed. Category comes from the
    form, priority stays the requester's, the workflow is the admin's "Used for"
    pick or the criteria match, and the owner follows from the category. The
    model only reads it afterwards (``aegis_read``) — a note for the owner that
    never changes these decisions or holds the workflow."""
    from app.intake import agents
    from app.intake.flow_agent import used_for_suggestion

    key = form_key(request)
    fv = request.field_values or {}
    name = next((f["name"] for f in _form_defs() if f["key"] == key), key)
    base = agents.result_for_category(_form_category(key, fv), 1.0, source="form")
    base["understanding"] = {
        "sub_type": fv.get("agreement_type") or name,
        "urgency": request.priority,
        "business_unit": fv.get("department") or request.department,
        "estimated_value": fv.get("value"),
        "jurisdiction": fv.get("governing_law"),
        "key_asks": [],
        "missing_info": [],
        "reasoning": f"Filed on the {name} form — type, value, dates and parties are as the requester stated them.",
    }
    base["needs_info"] = False  # the form's own required fields are the completeness check
    # Only a workflow set up for this type whose conditions hold; otherwise the
    # request waits for a person to pick — never a guess.
    base["flow_suggestion"] = used_for_suggestion(db, request) or {
        "flow_id": None, "flow_name": None, "confidence": 0.0, "alternatives": [], "needs_human": True,
        "source": "none", "reasoning": f"No workflow is set up for {request.type_label} requests that fits this one — pick one.",
    }
    base["read"] = aegis_read(db, request, claude_client=claude_client)
    return base


def _form_defs():
    from app.intake.agreement_forms import form_defs

    return form_defs()


_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "one or two sentences: what the requester actually wants"},
        "mismatches": {"type": "array", "items": {
            "type": "object",
            "properties": {"field": {"type": "string"},
                           "form_says": {"type": "string"},
                           "text_says": {"type": "string", "description": "what the description or attachment states instead"},
                           "same": {"type": "boolean", "description": "true if the two actually agree"}},
            "required": ["field", "form_says", "text_says", "same"]},
            "description": "facts the description or an attachment states differently from the form (type, value, date, party). Usually empty."},
        "bespoke_asks": {"type": "array", "items": {"type": "string"},
                         "description": "asks the standard template will not cover, e.g. counterparty paper, custom terms"},
        "negotiation_points": {"type": "array", "items": {"type": "string"},
                               "description": "terms the requester or an attachment signals will be negotiated"},
    },
    "required": ["summary", "mismatches", "bespoke_asks", "negotiation_points"],
}


def real_mismatches(items) -> list[str]:
    """The model lists fields that agree as "mismatches" however it is asked not
    to; making it state both sides lets these be dropped here."""
    return [
        f"{m.get('field')}: the form says {m.get('form_says')}; the text says {m.get('text_says')}"[:300]
        for m in (items or [])
        if isinstance(m, dict) and not m.get("same") and str(m.get("text_says") or "").strip()
    ][:6]


def aegis_read(db: Session, request: IntakeRequest, *, claude_client=None) -> dict | None:
    """The model's note on a form request, for the owner. Never raises and never
    changes the form's decisions: a failed or mocked read is simply absent."""
    from app.core.config import settings
    from app.core.database import utcnow

    if settings.mock_claude:
        return None
    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.integrations.claude import run_coro_blocking
    from app.integrations.dependencies import get_claude_client

    claude_client = claude_client or get_claude_client()
    try:
        bundle = get_agent_prompt(db, agent_id="intake_form_read", org_id=request.org_id)
        attachments = db.scalars(
            select(IntakeDocument).where(IntakeDocument.request_id == request.id)
            .order_by(IntakeDocument.created_at)
        ).all()
        user_prompt = _prompt(request, [], attachments).split("\nINTAKE REQUEST:", 1)[-1]
        # Without these the model read the form's bare "value: 4500000" as USD and
        # flagged a start date next week as suspiciously far off.
        context = (f"Today is {utcnow().date().isoformat()}. Form amounts are in "
                   f"{settings.default_currency} unless a field says otherwise.\n\n")
        resp = run_coro_blocking(lambda: claude_client.complete_structured(
            org_id=request.org_id,
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=context + "INTAKE REQUEST:" + user_prompt,
            tool_name="read_request", input_schema=_READ_SCHEMA,
            max_tokens=700, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(db, org_id=request.org_id, agent_id="intake_form_read", prompt_bundle=bundle,
                       input_payload={"user_prompt": user_prompt}, response=resp)
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = blocks[0].get("input") if blocks else None
        if not isinstance(data, dict):
            return None
        lists = ("bespoke_asks", "negotiation_points")
        return {"summary": str(data.get("summary") or "")[:600],
                "mismatches": real_mismatches(data.get("mismatches")),
                **{k: [str(x)[:300] for x in (data.get(k) or []) if str(x).strip()][:6] for k in lists}}
    except Exception:
        logger.warning("aegis read failed for %s", request.id, exc_info=True)
        return None


def _triage(db: Session, request: IntakeRequest, *, claude_client=None) -> dict:
    from app.core.config import settings
    from app.intake.flow_agent import flow_catalog

    catalog = flow_catalog(db, request.org_id)
    if settings.mock_claude:
        return _fallback(db, request)

    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.integrations.claude import run_coro_blocking
    from app.integrations.dependencies import get_claude_client

    claude_client = claude_client or get_claude_client()
    try:
        bundle = get_agent_prompt(db, agent_id="intake_triage", org_id=request.org_id)
        attachments = db.scalars(
            select(IntakeDocument).where(IntakeDocument.request_id == request.id)
            .order_by(IntakeDocument.created_at)
        ).all()
        user_prompt = _prompt(request, catalog, attachments)
        resp = run_coro_blocking(lambda: claude_client.complete_structured(
            org_id=request.org_id,
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
