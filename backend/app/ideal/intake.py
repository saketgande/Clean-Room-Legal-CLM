"""One front door: email, form or chat all become the same Request.

    arrives ─► understand ─► number + name ─► what's missing? ─► recommend ─► YOU choose
                (AI)                            (chase, capped)     (AI)      (human)

Two rules that shape everything here:

1. **Filing is never blocked.** A Request exists from the first second with a
   reference and an honest clock, even if half the details are missing. Waiting
   for a complete picture means a non-responsive sender makes the work invisible
   and the SLA data lie.

2. **AI is injected, never called.** ``understand`` and ``recommend`` take a
   function. Pass a model, pass a rule set, pass a stub in tests — the pipeline
   does not know or care. That keeps this module deterministic, keeps the model
   swappable, and means the expensive path can be measured against a cheap one.

The recommendation deliberately carries a summary, a confidence and the REASONS
behind it. A suggestion you cannot interrogate is not reviewable, and a person
has to confirm it before anything runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Channels a request can arrive through. The channel is metadata — it never
# forks the pipeline.
EMAIL, FORM, CHAT, API = "email", "form", "chat", "api"

# How many times we chase a sender for missing details before giving up and
# letting the request stand as incomplete.
# ponytail: flat cap. Per-field or per-channel policies can come later if asked.
MAX_CHASES = 2

CAPTURING, READY, INCOMPLETE = "capturing", "ready", "incomplete"


class IntakeError(ValueError):
    pass


@dataclass(frozen=True)
class Attachment:
    """A file that arrived with the request. ``text`` is already extracted —
    reading PDFs is not this module's job."""

    filename: str
    text: str = ""

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True)
class Inbound:
    """Whatever actually turned up, before we understand it."""

    channel: str
    sender: str
    subject: str = ""
    body: str = ""
    attachments: tuple[Attachment, ...] = ()

    @property
    def full_text(self) -> str:
        """Subject, body and every attachment — what the recommender reads.

        The attachments matter: a request whose subject says "NDA" but whose
        attached paper is a full manufacturing agreement is not an NDA, and
        reading only the subject line would route it wrongly.
        """
        parts = [self.subject, self.body, *(a.text for a in self.attachments if a.has_text)]
        return "\n\n".join(p for p in parts if p and p.strip())


@dataclass(frozen=True)
class Request:
    ref: str
    name: str
    channel: str
    sender: str
    fields: dict[str, str] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    chases: int = 0
    status: str = CAPTURING
    history: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class Recommendation:
    """What the AI thinks, and why — never what it decided."""

    workflow_key: str
    confidence: float
    summary: str
    reasons: tuple[str, ...]
    alternatives: tuple[dict, ...] = ()
    read_attachments: tuple[str, ...] = ()

    @property
    def needs_human(self) -> bool:
        """Low confidence is surfaced, not hidden. Either way a person confirms
        — this only says how loudly to ask."""
        return self.confidence < 0.75


def _derive_name(subject: str, fields: dict[str, str], type_label: str | None) -> str:
    """A name a person can recognise in a queue.

    Email subjects are unreliable — "FW: RE: quick question" tells nobody
    anything — so a counterparty and a type beat the raw subject when we have
    them.
    """
    counterparty = (fields.get("counterparty") or "").strip()
    kind = (type_label or fields.get("type") or "").strip()
    if kind and counterparty:
        return f"{kind} — {counterparty}"
    cleaned = subject.strip()
    for prefix in ("re:", "fw:", "fwd:"):
        while cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned or kind or "Untitled request"


def capture(
    inbound: Inbound,
    *,
    ref: str,
    understand,
    required: tuple[str, ...] = (),
) -> Request:
    """Turn an arrival into a Request. Always succeeds.

    ``understand(text, attachments) -> dict`` is injected: the extractor that
    pulls counterparty, value, dates and type out of unstructured text. Give it
    a model in production and a stub in tests.
    """
    if not (ref or "").strip():
        raise IntakeError("a request needs a reference")

    try:
        fields = dict(understand(inbound.full_text, inbound.attachments) or {})
    except Exception:
        # Extraction failing must not lose the request. It files with nothing
        # understood, and every required field shows as missing.
        fields = {}

    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    missing = tuple(f for f in required if not fields.get(f))

    return Request(
        ref=ref,
        name=_derive_name(inbound.subject, fields, fields.get("type")),
        channel=inbound.channel,
        sender=inbound.sender,
        fields=fields,
        missing=missing,
        attachments=inbound.attachments,
        status=READY if not missing else CAPTURING,
        history=(f"captured from {inbound.channel}",),
    )


def next_chase(request: Request) -> dict | None:
    """What to ask the sender for, or None if we should stop asking.

    Only email and chat can be chased — a form submitter has already gone. And
    after ``MAX_CHASES`` the request stands as incomplete rather than chasing
    someone forever.
    """
    if request.is_complete or request.status == INCOMPLETE:
        return None
    if request.channel not in (EMAIL, CHAT):
        return None
    if request.chases >= MAX_CHASES:
        return None
    return {
        "to": request.sender,
        "via": request.channel,
        "ref": request.ref,
        "ask_for": list(request.missing),
    }


def record_chase(request: Request) -> Request:
    """Note that we asked. Hitting the cap marks it incomplete and moves on."""
    chases = request.chases + 1
    status = INCOMPLETE if chases >= MAX_CHASES and request.missing else request.status
    note = f"chased for {', '.join(request.missing)} ({chases}/{MAX_CHASES})"
    return replace(request, chases=chases, status=status, history=request.history + (note,))


def apply_reply(request: Request, fields: dict[str, str]) -> Request:
    """Fold in what the sender sent back."""
    merged = {**request.fields, **{k: v for k, v in fields.items() if v not in (None, "")}}
    missing = tuple(f for f in request.missing if not merged.get(f))
    return replace(
        request,
        fields=merged,
        missing=missing,
        name=_derive_name(request.name, merged, merged.get("type")),
        status=READY if not missing else request.status,
        history=request.history + (f"reply added {', '.join(fields)}",),
    )


def recommend(request: Request, *, recommender) -> Recommendation:
    """Ask for a workflow suggestion, reading the WHOLE request.

    ``recommender(text, fields, attachments) -> dict`` is injected. It must
    return a workflow_key, a confidence, a summary and reasons — a bare answer
    is rejected, because an unexplained suggestion cannot be reviewed.
    """
    text = "\n\n".join(
        [request.name, *(a.text for a in request.attachments if a.has_text)]
    )
    raw = recommender(text, request.fields, request.attachments) or {}

    for key in ("workflow_key", "confidence", "summary"):
        if not raw.get(key):
            raise IntakeError(f"a recommendation must carry {key!r}")

    reasons = tuple(raw.get("reasons") or ())
    if not reasons:
        raise IntakeError("a recommendation must say WHY — an unexplained suggestion is not reviewable")

    return Recommendation(
        workflow_key=raw["workflow_key"],
        confidence=float(raw["confidence"]),
        summary=raw["summary"],
        reasons=reasons,
        alternatives=tuple(raw.get("alternatives") or ()),
        read_attachments=tuple(a.filename for a in request.attachments if a.has_text),
    )


@dataclass(frozen=True)
class Choice:
    """The human decision that actually starts work."""

    request_ref: str
    workflow_key: str
    chosen_by: str
    followed_recommendation: bool
    note: str | None = None


def choose(
    request: Request,
    recommendation: Recommendation,
    *,
    workflow_key: str,
    by: str,
    note: str | None = None,
) -> Choice:
    """A named person picks the workflow. Nothing runs until this happens.

    They may take the recommendation or override it; both are recorded, and the
    override rate is the honest measure of whether the recommender is any good.
    """
    if not (by or "").strip():
        raise IntakeError("a workflow choice must name who made it")
    return Choice(
        request_ref=request.ref,
        workflow_key=workflow_key,
        chosen_by=by,
        followed_recommendation=(workflow_key == recommendation.workflow_key),
        note=note,
    )


def notification(request: Request, rec: Recommendation) -> dict:
    """What the person is shown when asked to choose."""
    return {
        "ref": request.ref,
        "title": f"{request.ref} — {request.name}",
        "summary": rec.summary,
        "suggested": rec.workflow_key,
        "confidence": rec.confidence,
        "because": list(rec.reasons),
        "read": list(rec.read_attachments),
        "alternatives": list(rec.alternatives),
        "flagged_for_review": rec.needs_human,
        "missing_details": list(request.missing),
        "action": "Choose a workflow to start",
    }
