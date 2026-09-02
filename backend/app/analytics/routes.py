"""AI usage & cost analytics over the a_i_call_log ledger.

Cost is derived, not stored: the ledger records tokens per call, and we price
them here from a per-model rate table. Sonnet 4.5 (the model the app actually
runs on) is $3 / $15 per million input / output tokens.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.core.models import AICallLog

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Per-model list price, USD per 1M tokens (input, output). Matched by prefix so
# dated snapshots (claude-sonnet-4-5-20250929) resolve to their family.
_RATES: list[tuple[str, float, float]] = [
    ("claude-opus", 5.00, 25.00),
    ("claude-sonnet", 3.00, 15.00),
    ("claude-haiku", 1.00, 5.00),
    ("claude-fable", 10.00, 50.00),
]
_DEFAULT_RATE = (3.00, 15.00)  # assume Sonnet-tier for anything unrecognized


def _rate_for(model: str | None) -> tuple[float, float]:
    m = (model or "").lower()
    for prefix, in_rate, out_rate in _RATES:
        if m.startswith(prefix):
            return in_rate, out_rate
    return _DEFAULT_RATE


# prompt_key -> (human label, user-facing action category)
_LABELS: dict[str, tuple[str, str]] = {
    "intake_gate_classifier": ("Intake triage gate", "Raise a request"),
    "intake_triage": ("Intake triage", "Raise a request"),
    "contract_metadata_extraction": ("Metadata extraction", "Raise a request"),
    "contract_docx_generation": ("Auto-draft generation", "Raise a request"),
    "contract_edit_suggestions": ("Redline suggestions", "Redlining"),
    "playbook_review": ("Playbook deviation review", "Redlining"),
    "playbook_generation": ("Playbook generation", "Redlining"),
    "assistant_streaming": ("Ask Aegis (chat turn)", "Ask Aegis / Chat"),
    "contract_brain_answer": ("Contract Brain answer", "Ask Aegis / Chat"),
    "tabular_review_chat": ("Tabular review chat", "Ask Aegis / Chat"),
    "flow_router": ("Workflow router", "Ask Aegis / Chat"),
    "clause_extraction": ("Clause extraction", "Analysis & extraction"),
    "contract_risk_assessment": ("Risk assessment", "Analysis & extraction"),
    "tabular_cell_extraction": ("Tabular cell extraction", "Analysis & extraction"),
    "obligation_extraction": ("Obligation extraction", "Analysis & extraction"),
    "renewal_extraction": ("Renewal extraction", "Analysis & extraction"),
    "plain_language_summary": ("Plain-language summary", "Analysis & extraction"),
    "notice_response_agent": ("Notice response", "Analysis & extraction"),
}


def _label(prompt_key: str | None) -> tuple[str, str]:
    if not prompt_key:
        return ("Unlabeled", "Other")
    return _LABELS.get(prompt_key, (prompt_key.replace("_", " ").title(), "Other"))


def _cost(prompt_tokens: int, completion_tokens: int, model: str | None) -> float:
    in_rate, out_rate = _rate_for(model)
    return prompt_tokens / 1_000_000 * in_rate + completion_tokens / 1_000_000 * out_rate


@router.get("/ai-usage")
def ai_usage(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = (AICallLog.org_id == current_user.org_id, AICallLog.created_at >= since)

    # Per (operation, model): calls + token sums + avg latency.
    rows = db.execute(
        select(
            AICallLog.prompt_key,
            AICallLog.model,
            func.count().label("calls"),
            func.coalesce(func.sum(AICallLog.prompt_tokens), 0).label("in_tok"),
            func.coalesce(func.sum(AICallLog.completion_tokens), 0).label("out_tok"),
            func.avg(AICallLog.latency_ms).label("avg_latency"),
        )
        .where(*base)
        .group_by(AICallLog.prompt_key, AICallLog.model)
    ).all()

    by_operation: list[dict] = []
    by_category: dict[str, dict] = {}
    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    for r in rows:
        label, category = _label(r.prompt_key)
        cost = _cost(int(r.in_tok), int(r.out_tok), r.model)
        calls = int(r.calls)
        by_operation.append(
            {
                "prompt_key": r.prompt_key or "unlabeled",
                "label": label,
                "category": category,
                "model": r.model,
                "calls": calls,
                "prompt_tokens": int(r.in_tok),
                "completion_tokens": int(r.out_tok),
                "avg_prompt_tokens": round(int(r.in_tok) / calls) if calls else 0,
                "avg_completion_tokens": round(int(r.out_tok) / calls) if calls else 0,
                "avg_latency_ms": round(float(r.avg_latency), 1) if r.avg_latency else None,
                "cost": round(cost, 4),
                "cost_per_call": round(cost / calls, 4) if calls else 0.0,
            }
        )
        cat = by_category.setdefault(category, {"category": category, "calls": 0, "cost": 0.0})
        cat["calls"] += calls
        cat["cost"] += cost
        totals["calls"] += calls
        totals["prompt_tokens"] += int(r.in_tok)
        totals["completion_tokens"] += int(r.out_tok)
        totals["cost"] += cost

    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost"] = round(totals["cost"], 4)
    totals["cost_per_call"] = (
        round(totals["cost"] / totals["calls"], 4) if totals["calls"] else 0.0
    )

    by_operation.sort(key=lambda o: o["cost"], reverse=True)
    for c in by_category.values():
        c["cost"] = round(c["cost"], 4)

    # Daily cost trend. Cost needs the per-model rate, so group by day+model
    # and fold in Python.
    day = func.date(AICallLog.created_at)
    daily_rows = db.execute(
        select(
            day.label("day"),
            AICallLog.model,
            func.count().label("calls"),
            func.coalesce(func.sum(AICallLog.prompt_tokens), 0).label("in_tok"),
            func.coalesce(func.sum(AICallLog.completion_tokens), 0).label("out_tok"),
        )
        .where(*base)
        .group_by(day, AICallLog.model)
        .order_by(day)
    ).all()
    daily: dict[str, dict] = {}
    for r in daily_rows:
        key = str(r.day)
        d = daily.setdefault(key, {"date": key, "calls": 0, "cost": 0.0})
        d["calls"] += int(r.calls)
        d["cost"] += _cost(int(r.in_tok), int(r.out_tok), r.model)
    daily_list = [
        {**d, "cost": round(d["cost"], 4)} for d in sorted(daily.values(), key=lambda x: x["date"])
    ]

    return {
        "currency": "USD",
        "window_days": days,
        "totals": totals,
        "by_operation": by_operation,
        "by_category": sorted(by_category.values(), key=lambda c: c["cost"], reverse=True),
        "daily": daily_list,
        "rates": [
            {"model_family": p, "input_per_m": i, "output_per_m": o} for p, i, o in _RATES
        ],
    }
