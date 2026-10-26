"""Turning platform answers into what an agent sees: the compact `RequestSummary`, the triage
state and poll hint, fixed-template `next_step` text, REQ-ref lookup and the error table.

Everything here is a pure function of its inputs (no I/O), so it is tested without a platform.
Text written by people or other systems (subject, description, owner, document text) is copied
into JSON string fields as data. It is never interpreted, and `next_step` is built only from
status fields and the fixed templates below (FR-49, FR-50)."""

from __future__ import annotations

import re
from typing import Any

import httpx

from .aegis_client import UpstreamUnavailable
from .errors import ToolError

DEFAULT_RETRY_AFTER_SECONDS = 60
REF_PATTERN = re.compile(r"^REQ-\d{1,12}$")
_WHITESPACE = re.compile(r"\s+")
MAX_STEP_LABEL_CHARS = 80

# What the human-facing "Nothing was …" sentence says, per kind of tool call.
VERBS = {"read": "read", "create": "filed", "add": "added"}


# ---------------------------------------------------------------------------------------
# RequestSummary
# ---------------------------------------------------------------------------------------


def is_ref(request_id: str) -> bool:
    return bool(REF_PATTERN.match(request_id))


def find_by_ref(requests: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    """Exact `ref` match only; no prefix or case folding, so one REQ-12 never finds REQ-123."""
    for r in requests:
        if isinstance(r, dict) and r.get("ref") == ref:
            return r
    return None


def triage_state(request: dict[str, Any]) -> dict[str, Any]:
    """`{"state": "pending"}` until Aegis has an answer, then the answer as the platform gave
    it. Never a guess: a missing triage block is pending, not complete."""
    triage = request.get("ai_triage")
    if not isinstance(triage, dict) or not triage:
        return {"state": "pending"}
    status = triage.get("status")
    if status == "pending" or (status is None and "category" not in triage):
        return {"state": "pending"}
    suggestion = triage.get("flow_suggestion")
    workflow = suggestion.get("flow_name") if isinstance(suggestion, dict) else None
    return {
        "state": "complete",
        "category": triage.get("category"),
        "needs_info": bool(triage.get("needs_info")),
        "workflow": workflow,
    }


def _step_text(value: Any) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    return text[:MAX_STEP_LABEL_CHARS]


def _active_step(request: dict[str, Any]) -> dict[str, Any] | None:
    steps = request.get("workflow")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("active"):
            return step
    return None


def next_step(request: dict[str, Any], triage: dict[str, Any], poll_seconds: int) -> str:
    """The six fixed templates of the plan, in order."""
    status = request.get("status")
    if status in ("closed", "approved"):
        return f"This request is {status}. Nothing further is needed."
    if triage["state"] == "pending":
        return f"Aegis is triaging this request. Check again in about {poll_seconds} seconds."
    step = _active_step(request)
    if step is not None:
        label = _step_text(step.get("label") or step.get("name") or step.get("title"))
        stage = _step_text(step.get("stage"))
        return f"In workflow step '{label}' ({stage})."
    if triage.get("needs_info"):
        return "Legal needs more information; your user will be contacted."
    owner = request.get("assigned_to_label")
    if owner:
        return f"Assigned to {owner}. Legal will choose and start the workflow."
    return "Waiting in the Legal intake queue for an owner."


def request_summary(request: dict[str, Any], poll_seconds: int) -> dict[str, Any]:
    triage = triage_state(request)
    fields = request.get("field_values")
    form = fields.get("request_form") if isinstance(fields, dict) else None
    pending = triage["state"] == "pending"
    return {
        "id": request.get("id"),
        "ref": request.get("ref"),
        "subject": request.get("subject"),
        "form": form,
        "type_label": request.get("type_label"),
        "status": request.get("status"),
        "stage": request.get("stage"),
        "owner": request.get("assigned_to_label") or None,
        "sla_status": request.get("sla_status"),
        "submitted_at": request.get("submitted_at"),
        "triage": triage,
        "next_step": next_step(request, triage, poll_seconds),
        "poll_after_seconds": poll_seconds if pending else None,
    }


def request_detail(request: dict[str, Any], poll_seconds: int) -> dict[str, Any]:
    """`RequestSummary` plus the description, verbatim, as data."""
    out = request_summary(request, poll_seconds)
    out["description"] = request.get("description") or ""
    return out


# ---------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------

MAX_TEXT_CHARS = 8000


def shape_document(doc: dict[str, Any], *, include_text: bool) -> dict[str, Any]:
    """The platform's document, narrowed: `extracted_text` is dropped unless asked for and is
    never longer than the 8,000 characters the tool promises. This narrows, never widens."""
    out = dict(doc)
    if not include_text:
        out.pop("extracted_text", None)
    elif isinstance(out.get("extracted_text"), str):
        out["extracted_text"] = out["extracted_text"][:MAX_TEXT_CHARS]
    return out


# ---------------------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------------------


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _flatten_detail(detail: Any) -> str:
    """A 422 detail is a pydantic error list; flatten it to 'field: message; …'."""
    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, dict):
                loc = [str(p) for p in item.get("loc", ()) if p not in ("body", "query", "path")]
                msg = str(item.get("msg", "invalid value"))
                parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts)
    if detail is None:
        return ""
    return str(detail)


def _retry_after(response: httpx.Response, body: Any) -> int:
    if isinstance(body, dict):
        value = body.get("retry_after_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    header = response.headers.get("retry-after", "")
    if header.strip().isdigit() and int(header) > 0:
        return int(header)
    return DEFAULT_RETRY_AFTER_SECONDS


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def error_from_response(
    response: httpx.Response, *, kind: str = "read", idempotency_key: str | None = None
) -> ToolError:
    """Map a non-2xx platform answer to the plan's error table. `kind` is read | create |
    add and only changes the wording of the 429 message."""
    status = response.status_code
    body = _json(response)
    detail = _flatten_detail(body.get("detail")) if isinstance(body, dict) else ""
    request_id = None
    if isinstance(body, dict) and isinstance(body.get("request_id"), str):
        request_id = body["request_id"]
    request_id = request_id or response.headers.get("x-request-id")
    extra = {"platform_request_id": request_id}

    if status == 401:
        return ToolError(
            "authentication_failed",
            "Aegis did not accept this API key (it may be wrong, revoked, or its user "
            "deactivated). Nothing was done.",
            **extra,
        )
    if status == 403:
        why = detail or "permission denied"
        return ToolError(
            "permission_denied",
            f"Aegis refused: {why}. This agent's identity is not allowed to do that.",
            **extra,
        )
    if status == 404:
        return ToolError(
            "not_found", "No request with that id is visible to this agent.", **extra
        )
    if status == 409:
        return ToolError(
            "conflict",
            f"{_sentence(detail or 'Conflict')} The original request was left unchanged.",
            **extra,
        )
    if status == 413:
        return ToolError("too_large", detail or "The upload is too large for Aegis.", **extra)
    if status == 415:
        return ToolError(
            "unsupported_file_type", detail or "Aegis does not accept that file type.", **extra
        )
    if status == 422:
        return ToolError(
            "invalid_input",
            detail or "Aegis rejected the input as invalid.",
            **extra,
        )
    if status == 429:
        seconds = _retry_after(response, body)
        verb = VERBS.get(kind, "done")
        return ToolError(
            "rate_limited",
            f"Too many requests to Aegis. Try again in {seconds} seconds. Nothing was {verb}.",
            retry_after_seconds=seconds,
            **extra,
        )
    if status >= 500:
        if kind in ("create", "add"):
            # A write can fail after its commit, so a 5xx does not prove nothing happened.
            return error_from_unavailable(
                UpstreamUnavailable("status", status), kind=kind, idempotency_key=idempotency_key
            )
        return ToolError(
            "service_unavailable",
            "Aegis is unavailable right now. Nothing was done. Retry later.",
            **extra,
        )
    # Any other 4xx (400 …): the platform's own sentence, as invalid input.
    return ToolError(
        "invalid_input", detail or f"Aegis refused the request (HTTP {status}).", **extra
    )


def error_from_unavailable(
    _exc: UpstreamUnavailable, *, kind: str = "read", idempotency_key: str | None = None
) -> ToolError:
    """Connect error / timeout / 502-504 after retries. Reads cannot have changed anything, so
    they say 'unavailable'; a write may have landed, so it says 'outcome unknown'."""
    if kind == "create":
        return ToolError(
            "outcome_unknown",
            "Aegis did not confirm the filing. It may or may not have been filed. Call "
            f"create_intake_request again with idempotency_key '{idempotency_key}': a retry "
            "never files twice.",
            idempotency_key=idempotency_key,
        )
    if kind == "add":
        return ToolError(
            "outcome_unknown",
            "Aegis did not confirm the upload. Check get_request_documents before trying again.",
        )
    return ToolError(
        "service_unavailable",
        "Aegis is unavailable right now. Nothing was done. Retry later.",
    )
