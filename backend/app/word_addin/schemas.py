from typing import Literal

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    """A contract-review request from the Word task pane.

    ``text`` is the plain text the add-in pulls out of the open document; the
    rest is optional context that steers the review.
    """

    text: str = Field(min_length=1, description="Plain text of the contract pulled from Word.")
    title: str | None = Field(default=None, description="Document name — context only.")
    party: str | None = Field(
        default=None,
        description="Whose side we represent, e.g. 'We are the Customer'. Steers the redlines.",
    )
    playbook: str | None = Field(
        default=None,
        description="Optional free-text negotiation guidance / house positions to review against.",
    )
    max_findings: int = Field(default=12, ge=1, le=40)


class Finding(BaseModel):
    title: str
    severity: Literal["high", "medium", "low"] = "medium"
    category: str | None = None
    issue: str
    # Exact snippet copied verbatim from the contract so the add-in can locate
    # it with Word's search and replace it under tracked changes. Empty when the
    # finding is a missing clause (there is nothing to find).
    original_text: str | None = None
    # Replacement wording (action="replace") or a new clause (action="insert").
    suggested_text: str | None = None
    action: Literal["replace", "insert", "flag"] = "flag"
    rationale: str | None = None


class ReviewResponse(BaseModel):
    summary: str
    findings: list[Finding]
    model: str
    # True when the contract was longer than the per-call cap and was trimmed
    # before sending to the model — the add-in surfaces this to the user.
    truncated: bool = False


class AskRequest(BaseModel):
    """A free-form 'Ask Aegis' question about the open document."""

    question: str = Field(min_length=1, description="The user's question.")
    text: str = Field(min_length=1, description="Plain text of the contract pulled from Word.")
    title: str | None = Field(default=None, description="Document name — context only.")


class AskResponse(BaseModel):
    answer: str
    model: str
    truncated: bool = False


class LinkResponse(BaseModel):
    """Result of auto-linking the open Word document to a contract."""

    contract_id: str
    title: str | None = None
    lifecycle_stage: str | None = None
    # True when a new contract was created; False when the document matched an
    # existing one (same file content).
    created: bool = False
