"""Intake Copilot — conversational filing for complex/uncertain requests.

Deterministic interview (works with Claude mocked): classify from the running
transcript, extract who/what/when, ask the next missing thing, and gate
'ready to file' once enough is captured.

# ponytail: rule-based turn; swap turn() for a Claude call when live — the
# extracted-state + ready gate contract stays.
"""

from __future__ import annotations

import re

from app.intake import agents

_NEEDS_COUNTERPARTY = {"NDA", "Contract Review", "Vendor", "Trademark"}

# A proper-noun run: capitalised words (allowing &, ., -) joined by spaces or "and".
# Case-sensitive on purpose so "is Contoso" starts the match at "Contoso", not "is".
_NAME = r"[A-Z][\w&.\-]*(?:\s+(?:and\s+|&\s+)?[A-Z][\w&.\-]*){0,4}"


def _all_text(messages: list, user_message: str) -> str:
    parts = [m.content for m in messages] + [user_message]
    return " ".join(parts)


def _extract_counterparty(text: str) -> str | None:
    # "with/called/named Acme Corp", "with a vendor called Acme Corp"
    m = re.search(r"\b(?:with|called|named)\s+(?:a\s+\w+\s+called\s+)?(" + _NAME + r")", text)
    if m:
        return m.group(1).strip(" .,;")
    # "counterparty is Acme Corp", "counterparty: Acme Corp" — keyword is
    # case-insensitive but the captured name must be a proper-noun run, so it
    # never swallows the sentence ("is …") or spans past it.
    m = re.search(r"(?i:counterpart(?:y|ies))\s*(?:is|are|:|=|will be|would be)?\s+(" + _NAME + r")", text)
    return m.group(1).strip(" .,;") if m else None


def _extract_timing(text: str) -> str | None:
    for pat in [r"by (\w+ \d+)", r"(next week)", r"(this week)",
                # urgency, but not when negated ("not urgent", "isn't urgent")
                r"(?<!not )(?<!n't )\b(asap|urgent|emergency)\b",
                r"(end of (?:the )?(?:week|month|quarter))"]:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


def turn(messages: list, user_message: str) -> dict:
    text = _all_text(messages, user_message)
    cls = agents.classify(user_message, text)
    category = cls["category"]
    counterparty = _extract_counterparty(text)
    timing = _extract_timing(text)

    extracted = {"category": category}
    if counterparty:
        extracted["counterparty"] = counterparty
    if timing:
        extracted["timing"] = timing

    # Decide the next thing to ask.
    if category == "General":
        reply = ("Got it. What kind of legal help do you need — a contract or NDA, a privacy "
                 "question, a trademark, a dispute, or something else?")
        return {"reply": reply, "extracted": extracted, "ready": False, "suggested_type_label": None}

    suggested = f"{category} Request" if category != "Policy/FAQ" else "Policy question"
    if category in _NEEDS_COUNTERPARTY and not counterparty:
        reply = f"This looks like a {category} matter. Who is the counterparty (the other company)?"
        return {"reply": reply, "extracted": extracted, "ready": False, "suggested_type_label": suggested}
    if not timing:
        reply = f"Thanks. When do you need this {category.lower()} done — is there a deadline?"
        return {"reply": reply, "extracted": extracted, "ready": False, "suggested_type_label": suggested}

    who = f" with {counterparty}" if counterparty else ""
    reply = (f"Got it — a {category} request{who}, needed {timing}. I have what I need. "
             f"Review the summary and file it whenever you're ready.")
    return {"reply": reply, "extracted": extracted, "ready": True, "suggested_type_label": suggested}


if __name__ == "__main__":  # pragma: no cover - parser self-check
    # Guards the two bugs found in QA: negation flip and sentence-swallowing.
    assert _extract_timing("It's not urgent") is None
    assert _extract_timing("this is urgent") == "urgent"
    assert _extract_counterparty("The counterparty is Contoso Ltd.") == "Contoso Ltd"
    assert _extract_counterparty("with a vendor called Contoso Ltd for a pilot") == "Contoso Ltd"
    assert _extract_counterparty("counterparty: Ernst & Young LLP") == "Ernst & Young LLP"
    print("copilot extraction self-check passed")
