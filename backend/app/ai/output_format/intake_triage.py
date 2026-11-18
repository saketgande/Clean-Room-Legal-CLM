"""Output of the ``intake_triage`` feature (free-text requests: email, chat, API).

The field names, enums and descriptions are exactly what the hand-written dict in ``triage_agent`` sent
Claude before, so the model's side of the contract is unchanged; the Python side now has one typed
source instead of a dict plus ``in`` checks. The stored ``ai_triage`` shape is NOT this model: the
service maps it (``triage_agent._to_triage``), so the UI and database see no change.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    # Categories the rest of the system already understands (agents._CATEGORY_METADATA).
    NDA = "NDA"
    LITIGATION = "Litigation"
    PRIVACY = "Privacy"
    TRADEMARK = "Trademark"
    VENDOR = "Vendor"
    CONTRACT_REVIEW = "Contract Review"
    POLICY_FAQ = "Policy/FAQ"
    GENERAL = "General"


class Complexity(str, Enum):
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Urgency(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class IntakeTriageOutput(BaseModel):
    category: Category = Field(description=(
        "the single best-fit matter type, judged by meaning not keywords. NDA: confidentiality agreements. "
        "Vendor: buying goods/services from a supplier on our paper. Contract Review: any other agreement to "
        "draft or review (MSA, SoW, amendment, renewal, counterparty paper). Privacy: DPAs, data incidents, "
        "privacy questions. Litigation: disputes, claims, notices of breach, court papers. Trademark: marks "
        "and brand. Policy/FAQ: a question about a policy or how to do something. General: anything else"))
    sub_type: str | None = Field(None, description=(
        "the specific flavour, e.g. 'custom NDA (no template)', 'standard mutual NDA', 'MSA', 'DPA'"))
    complexity: Complexity = Field(description=(
        "judge from the ACTUAL request — deal value, bespoke terms, multi-party, cross-border all raise it; "
        "do not default by matter type"))
    risk: Risk
    urgency: Urgency = Field(description="how time-critical the requester makes it sound")
    business_unit: str | None = Field(None, description="the requesting business unit / department if stated or inferable")
    estimated_value: float | None = Field(None, description=(
        "the deal/contract value as stated (in its own currency, do not convert), else null"))
    jurisdiction: str | None = Field(None, description="governing law / country if stated")
    key_asks: list[str] = Field(default_factory=list, description=(
        "what the requester actually wants, in plain terms (e.g. 'draft a custom NDA, do not use the standard template')"))
    missing_info: list[str] = Field(default_factory=list, description=(
        "CRITICAL facts this matter type needs to be drafted or handled properly that the request does NOT provide — "
        "e.g. 'counterparty legal name', 'what the agreement is for'. Only facts WITHOUT which no one could start the "
        "work; anything negotiable or with a standard default (term, governing law, value) is NOT missing. Usually empty."))
    recommended_workflow_id: str | None = Field(None, description=(
        "id of the single best-fit workflow from the catalog, or null if none fits or a human should route it"))
    workflow_reasoning: str = Field("", description="one or two sentences on the workflow pick")
    confidence: float = Field(description="0..1 — honest confidence in this whole read")
    reasoning: str = Field(description="one or two sentences summarising your understanding of the request")
    needs_human: bool = Field(description=(
        "true if ambiguous or high-stakes enough that a human should confirm the routing"))
