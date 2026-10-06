"""AI usage & cost analytics over the a_i_call_log ledger.

Cost is derived, not stored: the ledger records tokens per call, and we price
them here from a per-model rate table (Sonnet is $3 / $15 per million input /
output tokens). Prompt-cache tokens are counted apart from input tokens by the
API and priced here too: a cache read at 0.1x the input rate, a cache write
(5-minute cache) at 1.25x. Rows written before the cache columns existed have
none recorded, so their cached input is not priced.

Labels come from the AI feature registry (app/ai/gateway/features.py).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.gateway.features import feature_registry
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


# prompt_key -> (human label, user-facing action category), from the registry.
_LABELS: dict[str, tuple[str, str]] = feature_registry.labels()


def _label(prompt_key: str | None) -> tuple[str, str]:
    if not prompt_key:
        return ("Unlabeled", "Other")
    return _LABELS.get(prompt_key, (prompt_key.replace("_", " ").title(), "Other"))


# Prompt-cache multipliers on the input rate (Anthropic list pricing).
_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25


def _cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    in_rate, out_rate = _rate_for(model)
    return (
        prompt_tokens * in_rate
        + completion_tokens * out_rate
        + cache_read * in_rate * _CACHE_READ_MULTIPLIER
        + cache_write * in_rate * _CACHE_WRITE_MULTIPLIER
    ) / 1_000_000


@router.get("/ai-usage")
def ai_usage(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    since = datetime.now(UTC) - timedelta(days=days)
    base = (AICallLog.org_id == current_user.org_id, AICallLog.created_at >= since)

    # Per (operation, model): calls + token sums + avg latency.
    rows = db.execute(
        select(
            AICallLog.prompt_key,
            AICallLog.model,
            func.count().label("calls"),
            func.coalesce(func.sum(AICallLog.prompt_tokens), 0).label("in_tok"),
            func.coalesce(func.sum(AICallLog.completion_tokens), 0).label("out_tok"),
            func.coalesce(func.sum(AICallLog.cache_read_input_tokens), 0).label("cache_read"),
            func.coalesce(func.sum(AICallLog.cache_creation_input_tokens), 0).label("cache_write"),
            func.avg(AICallLog.latency_ms).label("avg_latency"),
        )
        .where(*base)
        .group_by(AICallLog.prompt_key, AICallLog.model)
    ).all()

    by_operation: list[dict] = []
    by_category: dict[str, dict] = {}
    totals = {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "cost": 0.0,
    }
    for r in rows:
        label, category = _label(r.prompt_key)
        cost = _cost(int(r.in_tok), int(r.out_tok), r.model, int(r.cache_read), int(r.cache_write))
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
        totals["cache_read_tokens"] += int(r.cache_read)
        totals["cache_write_tokens"] += int(r.cache_write)
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
            func.coalesce(func.sum(AICallLog.cache_read_input_tokens), 0).label("cache_read"),
            func.coalesce(func.sum(AICallLog.cache_creation_input_tokens), 0).label("cache_write"),
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
        d["cost"] += _cost(int(r.in_tok), int(r.out_tok), r.model, int(r.cache_read), int(r.cache_write))
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
