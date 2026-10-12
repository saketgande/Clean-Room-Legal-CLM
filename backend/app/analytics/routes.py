"""AI usage & cost analytics over the a_i_call_log ledger.

Cost is derived, not stored: the ledger records tokens per call, and we price
them here from a per-model rate table (Sonnet 4.6 is $3 / $15 per million input /
output tokens, Sonnet 5.5 $2 / $10, Haiku 4.5 $1 / $5, Opus 5.5 $4 / $20).
Prompt-cache tokens are counted apart from input tokens by the API and priced
here too: a cache read at 0.1x the input rate, a cache write (5-minute cache) at
1.25x. Rows written before the cache columns existed have none recorded, so their
cached input is not priced. Calls answered through the Message Batches API
(``batched``) bill at half price on every token type.

``token_cost`` is public: the AI evaluation exam (specs/005-ai-eval-baseline)
prices its estimates and its run cost with this one table, so the exam's dollars
and this page's dollars cannot disagree. Exam calls carry ``eval_run_id`` and are
kept out of the default ("real") figures; ``traffic=exam`` shows them on their own.

Labels come from the AI feature registry (app/ai/gateway/features.py).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.gateway.features import feature_registry
from app.core.deps import get_db, require_permission
from app.core.models import AICallLog

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Per-model list price, USD per 1M tokens (input, output). Matched by prefix so
# dated snapshots (claude-sonnet-4-5-20250929) resolve to their model, and the
# FIRST match wins: model-specific rows come before the family fallbacks that
# price older / unlisted models of the same family.
_RATES: list[tuple[str, float, float]] = [
    ("claude-opus-5-5", 4.00, 20.00),
    ("claude-sonnet-5-5", 2.00, 10.00),
    ("claude-sonnet-4-6", 3.00, 15.00),
    ("claude-haiku-4-5", 1.00, 5.00),
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


# The Message Batches API bills every token type at half price; the cache
# multipliers stack on top of it.
_BATCH_MULTIPLIER = 0.5


def token_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None,
    cache_read: int = 0,
    cache_write: int = 0,
    *,
    batched: bool = False,
) -> float:
    in_rate, out_rate = _rate_for(model)
    cost = (
        prompt_tokens * in_rate
        + completion_tokens * out_rate
        + cache_read * in_rate * _CACHE_READ_MULTIPLIER
        + cache_write * in_rate * _CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
    return cost * _BATCH_MULTIPLIER if batched else cost


# Pre-exam name, still imported by tests/test_ai_gateway.py.
_cost = token_cost


def _sum(column):
    return func.coalesce(func.sum(column), 0)


@router.get("/ai-usage")
def ai_usage(
    days: int = Query(30, ge=1, le=365),
    traffic: Literal["real", "exam"] = Query("real"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    since = datetime.now(UTC) - timedelta(days=days)
    # Exam calls carry eval_run_id (FR-34). Real usage is the default so the
    # figures everyone already reads stop including exam spend.
    exam_rows = (
        AICallLog.eval_run_id.is_not(None) if traffic == "exam" else AICallLog.eval_run_id.is_(None)
    )
    base = (AICallLog.org_id == current_user.org_id, AICallLog.created_at >= since, exam_rows)

    # Per (operation, model, batched): calls + token sums + latency. Batched rows
    # are priced at half, so they are grouped apart and folded back below.
    rows = db.execute(
        select(
            AICallLog.prompt_key,
            AICallLog.model,
            AICallLog.batched,
            func.count().label("calls"),
            _sum(AICallLog.prompt_tokens).label("in_tok"),
            _sum(AICallLog.completion_tokens).label("out_tok"),
            _sum(AICallLog.cache_read_input_tokens).label("cache_read"),
            _sum(AICallLog.cache_creation_input_tokens).label("cache_write"),
            func.sum(AICallLog.latency_ms).label("latency_sum"),
            func.count(AICallLog.latency_ms).label("latency_n"),
        )
        .where(*base)
        .group_by(AICallLog.prompt_key, AICallLog.model, AICallLog.batched)
    ).all()

    # One entry per (prompt_key, model), whatever mix of batched / live rows fed it.
    ops: dict[tuple[str | None, str], dict] = {}
    for r in rows:
        op = ops.setdefault(
            (r.prompt_key, r.model),
            {"calls": 0, "in_tok": 0, "out_tok": 0, "cache_read": 0, "cache_write": 0,
             "latency_sum": 0.0, "latency_n": 0, "cost": 0.0},
        )
        op["calls"] += int(r.calls)
        op["in_tok"] += int(r.in_tok)
        op["out_tok"] += int(r.out_tok)
        op["cache_read"] += int(r.cache_read)
        op["cache_write"] += int(r.cache_write)
        op["latency_sum"] += float(r.latency_sum or 0)
        op["latency_n"] += int(r.latency_n)
        op["cost"] += token_cost(
            int(r.in_tok), int(r.out_tok), r.model, int(r.cache_read), int(r.cache_write),
            batched=r.batched,
        )

    by_operation: list[dict] = []
    by_category: dict[str, dict] = {}
    totals = {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "cost": 0.0,
    }
    for (prompt_key, model), o in ops.items():
        label, category = _label(prompt_key)
        calls = o["calls"]
        by_operation.append(
            {
                "prompt_key": prompt_key or "unlabeled",
                "label": label,
                "category": category,
                "model": model,
                "calls": calls,
                "prompt_tokens": o["in_tok"],
                "completion_tokens": o["out_tok"],
                "avg_prompt_tokens": round(o["in_tok"] / calls) if calls else 0,
                "avg_completion_tokens": round(o["out_tok"] / calls) if calls else 0,
                "avg_latency_ms": (
                    round(o["latency_sum"] / o["latency_n"], 1) if o["latency_sum"] else None
                ),
                "cost": round(o["cost"], 4),
                "cost_per_call": round(o["cost"] / calls, 4) if calls else 0.0,
            }
        )
        cat = by_category.setdefault(category, {"category": category, "calls": 0, "cost": 0.0})
        cat["calls"] += calls
        cat["cost"] += o["cost"]
        totals["calls"] += calls
        totals["prompt_tokens"] += o["in_tok"]
        totals["completion_tokens"] += o["out_tok"]
        totals["cache_read_tokens"] += o["cache_read"]
        totals["cache_write_tokens"] += o["cache_write"]
        totals["cost"] += o["cost"]

    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost"] = round(totals["cost"], 4)
    totals["cost_per_call"] = (
        round(totals["cost"] / totals["calls"], 4) if totals["calls"] else 0.0
    )

    by_operation.sort(key=lambda o: o["cost"], reverse=True)
    for c in by_category.values():
        c["cost"] = round(c["cost"], 4)

    # Daily cost trend. Cost needs the per-model rate and the batch flag, so
    # group by day+model+batched and fold in Python.
    day = func.date(AICallLog.created_at)
    daily_rows = db.execute(
        select(
            day.label("day"),
            AICallLog.model,
            AICallLog.batched,
            func.count().label("calls"),
            _sum(AICallLog.prompt_tokens).label("in_tok"),
            _sum(AICallLog.completion_tokens).label("out_tok"),
            _sum(AICallLog.cache_read_input_tokens).label("cache_read"),
            _sum(AICallLog.cache_creation_input_tokens).label("cache_write"),
        )
        .where(*base)
        .group_by(day, AICallLog.model, AICallLog.batched)
        .order_by(day)
    ).all()
    daily: dict[str, dict] = {}
    for r in daily_rows:
        key = str(r.day)
        d = daily.setdefault(key, {"date": key, "calls": 0, "cost": 0.0})
        d["calls"] += int(r.calls)
        d["cost"] += token_cost(
            int(r.in_tok), int(r.out_tok), r.model, int(r.cache_read), int(r.cache_write),
            batched=r.batched,
        )
    daily_list = [
        {**d, "cost": round(d["cost"], 4)} for d in sorted(daily.values(), key=lambda x: x["date"])
    ]

    return {
        "currency": "USD",
        "window_days": days,
        "traffic": traffic,
        "totals": totals,
        "by_operation": by_operation,
        "by_category": sorted(by_category.values(), key=lambda c: c["cost"], reverse=True),
        "daily": daily_list,
        "rates": [
            {"model_family": p, "input_per_m": i, "output_per_m": o} for p, i, o in _RATES
        ],
    }
