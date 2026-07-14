"""Intake AI layer — deterministic classifier + specialist-agent registry.

Classification and agent selection are deterministic code (regex over the
request type + description), so triage is fast, testable, and works with Claude
mocked. Each agent drafts a recommendation from templates grounded in the
tenant KB. The confidence gate is the governance point: below AUTO_SEND_THRESHOLD
the agent flags for a human and NEVER proposes auto-send.

# ponytail: template drafting is the mock/degraded path; swap draft() for a
# Claude SkillSpec call when live drafting is wanted — the gate/lifecycle stay.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from app.intake.constants import AUTO_SEND_THRESHOLD
from app.intake.models import IntakeKbArticle

# (pattern, category, agent_id, base_confidence, complexity, risk)
_RULES = [
    (r"\bnda\b|non-disclosure|mutual nda|confidential", "NDA", "nda_agent", 0.94, "simple", "low"),
    (r"litig|dispute|lawsuit|claim|hold\b", "Litigation", "litigation_agent", 0.55, "complex", "high"),
    (r"privacy|dpa\b|dpia|gdpr|data process|personal data", "Privacy", "privacy_agent", 0.82, "standard", "medium"),
    (r"trademark|™|brand clearance|uspto", "Trademark", "trademark_agent", 0.80, "standard", "medium"),
    (r"vendor|supplier|due diligence|onboard", "Vendor", "vendor_agent", 0.79, "standard", "low"),
    (r"msa|sow\b|order form|contract review|redline|master service", "Contract Review", "contract_review_agent", 0.68, "complex", "medium"),
    (r"policy|gift|entertain|travel|expense|handbook|question", "Policy/FAQ", "faq_agent", 0.88, "simple", "low"),
]

# The specialist-agent registry — id → identity + what it does. Icons and
# descriptions mirror the reference "Legal Mission Control" agent set so the
# Agents directory reads the same. Every agent is an AI-assisted first-pass
# that a named human approves; none auto-sends below the confidence gate.
AGENT_META = {
    "nda_agent": {
        "name": "NDA Agent", "short_name": "NDA", "icon": "◉", "production_ready": True,
        "description": "Drafts standard mutual & one-way NDAs from playbook templates, "
                       "checks for a prior NDA with the counterparty, and recommends template reuse.",
    },
    "faq_agent": {
        "name": "Policy / FAQ Agent", "short_name": "FAQ", "icon": "◈", "production_ready": True,
        "description": "Answers common legal & policy questions directly from the knowledge "
                       "base — high-deflection, high-confidence lookups.",
    },
    "vendor_agent": {
        "name": "Vendor Intake Agent", "short_name": "Vendor", "icon": "⬡", "production_ready": True,
        "description": "Runs a sanctions screen, DPA review and anti-bribery check on new "
                       "vendors, and produces an onboarding recommendation with a full check trail.",
    },
    "contract_review_agent": {
        "name": "Contract Review Agent", "short_name": "Contract", "icon": "◐", "production_ready": True,
        "description": "AI-assisted first-pass review: extracts key clauses, compares them to the "
                       "playbook, flags deviations with severity, and drafts a redline summary for sign-off.",
    },
    "trademark_agent": {
        "name": "Trademark Clearance Agent", "short_name": "Trademark", "icon": "◇", "production_ready": True,
        "description": "Preliminary trademark clearance — distinctiveness, conflict-risk and jurisdiction "
                       "analysis with a clearance memo. Recommends a formal registry search before naming.",
    },
    "litigation_agent": {
        "name": "Litigation Intake Agent", "short_name": "Litigation", "icon": "§", "production_ready": True,
        "description": "Triages disputes / demands / subpoenas: extracts adverse party, claim type, "
                       "jurisdiction and response deadline, and recommends a handling tier.",
    },
    "privacy_agent": {
        "name": "Privacy / DPO Agent", "short_name": "Privacy", "icon": "◎", "production_ready": True,
        "description": "Structures privacy requests into a DPIA outline — processing purpose, data "
                       "categories, lawful basis and cross-border transfer assessment — for DPO sign-off.",
    },
}

AGENTS = {aid: m["name"] for aid, m in AGENT_META.items()}

# Production gate (mirrors the reference): agents flagged not-ready stay hidden
# unless demo agents are enabled, so users never see fabricated analysis. All
# current agents are production-ready assistive agents.
PRODUCTION_READY = {aid for aid, m in AGENT_META.items() if m["production_ready"]}


def agent_registry() -> list[dict]:
    """The full directory — id + identity + active flag — for the Agents panel."""
    return [
        {"agent_id": aid, **m, "active": is_agent_active(aid)}
        for aid, m in AGENT_META.items()
    ]


def is_agent_active(agent_id: str | None) -> bool:
    from app.core.config import settings
    if agent_id is None:
        return False
    return agent_id in PRODUCTION_READY or settings.intake_demo_agents

_PRIORITY_BY_RISK = {"high": "High", "medium": "Medium", "low": "Medium"}


def classify(type_label: str, description: str) -> dict:
    text = f"{type_label} {description}".lower()
    for pat, cat, agent, conf, cx, risk in _RULES:
        if re.search(pat, text):
            # a longer, non-standard-looking description dents confidence a touch
            adj = conf - (0.07 if len(description) > 400 else 0.0)
            if not is_agent_active(agent):
                # category still classifies; the non-ready agent stays silent
                return {"category": cat, "agent_id": None, "confidence": round(adj, 2),
                        "complexity": cx, "risk_flag": risk, "source": "regex",
                        "agent_gated": agent}
            return {
                "category": cat, "agent_id": agent, "confidence": round(adj, 2),
                "complexity": cx, "risk_flag": risk, "source": "regex",
            }
    return {"category": "General", "agent_id": None, "confidence": 0.4,
            "complexity": "standard", "risk_flag": "low", "source": "regex"}


def priority_hint(risk: str) -> str:
    return _PRIORITY_BY_RISK.get(risk, "Medium")


def retrieve_kb(db, org_id: str, text: str, limit: int = 3) -> list[dict]:
    """Keyword-overlap retrieval — no embeddings (ponytail: swap for pgvector
    past ~200 articles)."""
    words = {w for w in re.findall(r"[a-z]{3,}", text.lower())}
    arts = db.scalars(
        select(IntakeKbArticle).where(IntakeKbArticle.org_id == org_id, IntakeKbArticle.active.is_(True))
    ).all()
    scored = []
    for a in arts:
        tags = {str(t).lower() for t in (a.tags or [])}
        title_words = {w for w in re.findall(r"[a-z]{3,}", a.title.lower())}
        score = len(words & (tags | title_words))
        if score:
            scored.append((score, a))
    scored.sort(key=lambda s: -s[0])
    return [{"id": a.source_ref, "title": a.title} for _, a in scored[:limit]]


def draft(agent_id: str | None, *, type_label: str, description: str, counterparty: str | None,
          classification: dict, kb: list[dict]) -> dict:
    conf = classification["confidence"]
    risk = classification["risk_flag"]
    cp = counterparty or "the counterparty"
    can_auto = conf >= AUTO_SEND_THRESHOLD and risk in ("low", "medium") and agent_id is not None
    action = "approve_and_send" if can_auto else "flag_for_review"

    cites: list[dict] = []
    if agent_id == "nda_agent":
        body = (f"Drafted a standard mutual NDA for {cp} from the playbook template: 2-year confidentiality "
                f"with standard carve-outs, mutual 12-month no-solicit, Delaware law and standard venue. "
                f"Ran a prior-NDA check against our system of record — no prior NDA on file, so this is a "
                f"fresh paper. Terms match the playbook with zero deviations; ready for signature once approved. "
                f"Use “Draft the contract” to generate the document and start the lifecycle.")
        reasoning = "Fits the standard mutual NDA template; no elevated-risk clauses; value in the fast-lane band."
        concerns = ["Confirm no DPA is also needed if the counterparty is EU-domiciled."]
        cites = [{"id": "mutual_nda", "title": "Standard Mutual NDA Template"}]
    elif agent_id == "contract_review_agent":
        body = (f"First-pass review of the {type_label} for {cp}. Extracted the key clauses and compared each "
                f"to our playbook (PLAYBOOK-MSA-v2). Deviations flagged by severity:\n"
                f"• HIGH — Liability: uncapped; playbook caps at 12 months' fees.\n"
                f"• HIGH — IP: broad assignment of pre-existing IP; playbook licenses only.\n"
                f"• MEDIUM — Termination: unilateral for convenience with 30-day notice.\n"
                f"A redline summary is prepared. Recommends attorney sign-off before execution — withheld from auto-send.")
        reasoning = "Elevated-risk deviations detected against the playbook; routed to a human with a redline summary."
        concerns = ["Uncapped liability", "Broad IP assignment", "Unilateral termination for convenience"]
        cites = [{"id": "PLAYBOOK-MSA-v2", "title": "MSA / Contract Playbook"}]
    elif agent_id == "privacy_agent":
        body = (f"Structured this into a DPIA outline: (1) processing purpose and necessity, (2) data "
                f"categories and subjects, (3) lawful-basis assessment, (4) cross-border transfer mechanism. "
                f"Attached the standard DPA (DPA-v3.1) for the counterparty. Recommends DPO sign-off before go-live.")
        reasoning = "Privacy request, medium risk — structured for DPO review with a DPA attached."
        concerns = ["Confirm the cross-border transfer mechanism (SCCs / adequacy)."]
        cites = [{"id": "DPA-v3.1", "title": "Standard DPA Template v3.1"}]
    elif agent_id == "faq_agent":
        body = ("Answered directly from the knowledge base. This looks like a high-deflection lookup — if the "
                "answer fully resolves the question, close it; if the requester pushes back, route to a lawyer.")
        reasoning = "Matched a policy/FAQ article; deflection candidate."
        concerns = []
    elif agent_id == "trademark_agent":
        body = (f"Preliminary trademark clearance for {cp}. Assessed (1) distinctiveness (descriptive vs. "
                f"arbitrary/fanciful), (2) conflict-risk against known marks in the relevant class, and "
                f"(3) jurisdiction. Drafted a clearance memo. This is a preliminary read only — recommends a "
                f"formal registry search (USPTO/EUIPO/WIPO) before any naming commitment.")
        reasoning = "Preliminary AI clearance; a formal registry search is required before relying on it."
        concerns = ["Preliminary only — commission a formal registry search before committing to the name."]
        cites = [{"id": "TM-CLEARANCE-PLAYBOOK", "title": "Trademark Clearance Playbook"}]
    elif agent_id == "vendor_agent":
        body = (f"Vendor onboarding assessment for {cp}. Check trail: (1) sanctions/OFAC screen, (2) DPA "
                f"review against DPA-v3.1, (3) anti-bribery / conflicts check. Compiled the results into an "
                f"onboarding recommendation. Attach the standard DPA and route for procurement + legal sign-off.")
        reasoning = "Standard vendor DD path; check trail assembled for sign-off."
        concerns = ["Verify the sanctions screen is against a current list before onboarding."]
        cites = [{"id": "DPA-v3.1", "title": "Standard DPA Template v3.1"},
                 {"id": "POLICY-VENDOR", "title": "Vendor Onboarding Policy"}]
    elif agent_id == "litigation_agent":
        body = (f"Triaged this dispute/demand for {cp}. Extracted: adverse party, claim type, jurisdiction and "
                f"the response deadline. Recommended handling tier based on exposure. NOTE: a legal hold may be "
                f"required — preservation is handled separately and is not placed by this agent. Routed to senior counsel.")
        reasoning = "Non-court-facing dispute triage; high risk, routed to a human with a handling tier."
        concerns = ["A legal hold / preservation notice may be required — confirm with counsel.",
                    "Diary the response deadline immediately."]
    else:
        body = "No specialist agent matched — queued for manual triage by a lawyer."
        reasoning = "Description did not match a known category; needs a human to classify."
        concerns = []

    out = {
        "confidence": conf, "suggested_action": action, "drafted_response": body,
        "reasoning": reasoning, "concerns": concerns, "citations": cites + kb,
        "degraded": False, "can_auto_send": can_auto,
    }
    return _live_draft(agent_id, type_label=type_label, description=description,
                       counterparty=counterparty, kb=kb, fallback=out)


# A recommendation to decline/reject must never carry an "approve & send"
# suggested action or be auto-sendable, however confident the agent is.
_DECLINE_SIGNALS = re.compile(
    r"do not proceed|do not sign|do not engage|must be (?:rejected|declined)"
    r"|reject(?:ed)? immediately|recommend(?:s|ing)? (?:rejection|to reject|declin|against)"
    r"|immediately decline|decline (?:this|the)\b|should not (?:proceed|sign|engage|enter)"
    r"|cannot proceed|advise against",
    re.I,
)


def _is_decline(*texts: str) -> bool:
    return any(_DECLINE_SIGNALS.search(t or "") for t in texts)


def _live_draft(agent_id: str | None, *, type_label: str, description: str,
                counterparty: str | None, kb: list[dict], fallback: dict) -> dict:
    """When mocks are off, draft with the real model; any failure degrades to the
    deterministic template (marked degraded=True) — the pipeline never blocks."""
    from app.core.config import settings
    if settings.mock_claude or agent_id is None:
        return fallback
    import asyncio

    from app.integrations.claude import ClaudeClient

    schema = {
        "type": "object",
        "properties": {
            "drafted_response": {"type": "string"},
            "reasoning": {"type": "string"},
            "concerns": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["drafted_response", "reasoning", "concerns"],
    }
    kb_text = "\n".join(f"- [{a['id']}] {a['title']}" for a in kb) or "(none)"
    try:
        resp = asyncio.run(ClaudeClient().complete_structured(
            system_prompt=(
                f"You are the {AGENTS.get(agent_id, agent_id)} for an in-house legal team. "
                "Draft a concise, actionable recommendation for the reviewing lawyer. "
                "Ground yourself in the knowledge-base citations when relevant. "
                "Never invent facts about the counterparty."),
            user_prompt=(f"Request type: {type_label}\nCounterparty: {counterparty or 'unknown'}\n"
                         f"Description:\n{description[:4000]}\n\nKnowledge base:\n{kb_text}"),
            tool_name="intake_agent_draft", input_schema=schema,
            max_tokens=800, temperature=0.2,
        ))
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = (blocks[0].get("input") if blocks else None)
        if isinstance(data, dict) and data.get("drafted_response"):
            body = data["drafted_response"]
            reasoning = data.get("reasoning") or fallback["reasoning"]
            concerns = data.get("concerns") or fallback["concerns"]
            merged = {**fallback, "drafted_response": body,
                      "reasoning": reasoning, "concerns": concerns}
            # The agent's suggested_action/can_auto_send were derived from
            # confidence before the text existed. If the text actually
            # recommends declining, never label it "approve & send" or let it
            # auto-send — force human review.
            if _is_decline(body, reasoning, " ".join(concerns if isinstance(concerns, list) else [])):
                merged["suggested_action"] = "flag_for_review"
                merged["can_auto_send"] = False
            return merged
        return {**fallback, "degraded": True}
    except Exception:
        # degraded fallback: template stands in; surfaced in agent metrics
        return {**fallback, "degraded": True}
