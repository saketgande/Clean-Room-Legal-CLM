"""Tier-0 hard gates — the derived approval matrix (from the aegis reference).

A gate fires on a request's text/fields and FORCES a senior rung into the
approval ladder regardless of value:

  exclusivity          → Legal Counsel
  clinical trial       → Legal Counsel   (always)
  sensitive personal   → Compliance/DPO  (always)
  regulated claim      → Compliance      (never agent-cleared)
  litigation / breach  → Executive/GC    (+ Critical priority + Escalated)

Detection is an AI classifier with a deterministic keyword fallback (used when
Claude is mocked, degraded, or errors). A human can add or remove any gate — the
override always wins and is audited. Effective gates = AI-detected − removed +
added; they resolve to forced ladder rungs in intake/approval_bridge.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.intake.constants import PRIORITIES


@dataclass(frozen=True)
class Gate:
    key: str
    label: str
    approver_group: str  # existing ApproverGroup name forced as a rung
    keywords: tuple[str, ...]  # deterministic fallback detector (lowercased substrings)
    set_priority: str | None = None  # side-effect: raise priority to (never lowers)
    set_status: str | None = None  # side-effect: move to status (only 'escalated' used)


GATES: tuple[Gate, ...] = (
    Gate("exclusivity", "Exclusivity / exclusive licence", "Legal Counsel",
         ("exclusive", "exclusivity", "exclusive licen", "sole supplier", "sole source")),
    Gate("clinical", "Clinical-trial agreement", "Legal Counsel",
         ("clinical trial", "investigator site", "study protocol", "irb approval",
          "clinical study", "clinical investigation")),
    Gate("sensitive_data", "Sensitive personal data", "Compliance",
         ("health record", "medical record", "biometric", "genetic data", "patient data",
          "protected health", "special category", "phi")),
    Gate("regulated_marketing", "Regulated / therapeutic claim", "Compliance",
         ("fda approved", "fda-approved", "clinically proven", "prevents infection",
          "cures ", "therapeutic claim", "medical device")),
    Gate("litigation", "Litigation / breach-termination", "Executive",
         ("litigation", "notice of breach", "termination for cause", "lawsuit",
          "subpoena", "demand letter", "cease and desist", "breach of contract"),
         set_priority="Critical", set_status="escalated"),
)
GATE_BY_KEY: dict[str, Gate] = {g.key: g for g in GATES}


def _request_text(request) -> str:
    parts = [request.type_label or "", request.description or ""]
    fv = request.field_values or {}
    parts += [str(v) for v in fv.values() if isinstance(v, (str, int, float))]
    return " ".join(parts)


def detect_keyword(text: str) -> list[dict]:
    """Deterministic fallback — substring match. Always available."""
    low = text.lower()
    out = []
    for g in GATES:
        hit = next((kw for kw in g.keywords if kw in low), None)
        if hit:
            out.append({"key": g.key, "label": g.label, "confidence": 0.5,
                        "matched_text": hit.strip(), "source": "keyword"})
    return out


def _classify_ai(db, org_id: str, text: str) -> list[dict] | None:
    """One structured Claude pass — mirrors intake/agents._live_draft. Returns
    None when mocked/degraded/error so the caller falls back to keywords."""
    from app.core.config import settings

    if settings.mock_claude:
        return None
    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import ClaudeClient, run_coro_blocking

    schema = {
        "type": "object",
        "properties": {
            "gates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": [g.key for g in GATES]},
                        "confidence": {"type": "number"},
                        "matched_text": {"type": "string"},
                    },
                    "required": ["key", "confidence"],
                },
            }
        },
        "required": ["gates"],
    }
    catalog = "\n".join(f"- {g.key}: {g.label}" for g in GATES)
    bundle = get_agent_prompt(db, agent_id="intake_gate_classifier", org_id=org_id)
    user_prompt = text[:4000]
    try:
        enforce_daily_token_cap(org_id)
        resp = run_coro_blocking(lambda: ClaudeClient().complete_structured(
            system_prompt=bundle.skill_prompt + "\nGates:\n" + catalog + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=user_prompt,
            tool_name="intake_gate_classifier", input_schema=schema,
            max_tokens=500, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(db, org_id=org_id, agent_id="intake_gate_classifier", prompt_bundle=bundle,
                        input_payload={"user_prompt": user_prompt}, response=resp)
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = blocks[0].get("input") if blocks else None
        if not isinstance(data, dict):
            return None
        out = []
        for row in data.get("gates", []):
            g = GATE_BY_KEY.get(row.get("key"))
            if g is None:
                continue
            out.append({
                "key": g.key, "label": g.label,
                "confidence": round(float(row.get("confidence", 0.6)), 2),
                "matched_text": str(row.get("matched_text") or "")[:200],
                "source": "ai",
            })
        return out
    except Exception:
        return None


def classify_gates(db, request) -> list[dict]:
    """AI classifier with a deterministic keyword fallback."""
    text = _request_text(request)
    # M5: `_classify_ai(...) or detect_keyword(...)` treated a *successful* empty
    # result (AI ran, found no gates → []) as a failure and fell through to the
    # keyword detector, over-gating cleared requests. _classify_ai returns None
    # ONLY on mock/degraded/error; an empty list means "no gates apply".
    ai = _classify_ai(db, request.org_id, text)
    return ai if ai is not None else detect_keyword(text)


def effective_gate_keys(ai_triage: dict | None) -> list[str]:
    """AI-detected gates minus human-removed plus human-added, in catalog order."""
    ai_triage = ai_triage or {}
    keys = {g["key"] for g in ai_triage.get("gates", []) if g.get("key") in GATE_BY_KEY}
    for ov in ai_triage.get("gate_overrides", []):
        if ov.get("action") == "add" and ov.get("gate_key") in GATE_BY_KEY:
            keys.add(ov["gate_key"])
        elif ov.get("action") == "remove":
            keys.discard(ov.get("gate_key"))
    return [g.key for g in GATES if g.key in keys]  # stable catalog order


def effective_gates(ai_triage: dict | None) -> list[Gate]:
    return [GATE_BY_KEY[k] for k in effective_gate_keys(ai_triage)]


def _prio_rank(p: str | None) -> int:
    try:
        return PRIORITIES.index(p)
    except ValueError:
        return -1


def apply_gate_side_effects(request) -> None:
    """Raise priority / escalate per the fired gates. Direct field writes — the
    caller (create/override) owns the audit + commit. Never lowers priority,
    never downgrades a terminal/escalated request."""
    for g in effective_gates(request.ai_triage):
        if g.set_priority and _prio_rank(g.set_priority) > _prio_rank(request.priority):
            request.priority = g.set_priority
        if g.set_status == "escalated" and request.status not in (
            "escalated", "closed", "approved"
        ):
            request.status = "escalated"
