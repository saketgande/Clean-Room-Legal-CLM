"""Intake classifier — deterministic category + risk + complexity.

A regex over the request type + description picks a category, a risk flag and a
complexity, fast and testable. This still feeds routing rules, the workflow
ai_task steps, and the Tier-0 gate matrix. (The AI recommendation drafting was
removed with triage — a request flows straight to its workflow.)
"""

from __future__ import annotations

import re

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

# Production gate: agents flagged not-ready stay hidden unless demo agents are
# enabled, so the classifier never selects a non-ready specialist.
PRODUCTION_READY = {aid for aid, m in AGENT_META.items() if m["production_ready"]}


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
