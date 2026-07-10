"""Aegis AI quality harness.

Runs the golden set (eval/golden.json) against the REAL retrieval + answer
functions the app uses, and prints a scorecard. This is the keystone that
turns "I feel it's weak" into numbers you can track across changes.

Usage (inside the backend container):
    python eval/run_eval.py                # retrieval + taxonomy (free, no LLM)
    python eval/run_eval.py --faithfulness # also generate answers + verify cites (costs tokens)

Metrics
    retrieval.hit_rate       — % of queries that returned >= min_sources sources
    retrieval.keyword_recall — % of queries where an expected keyword appears in
                               the retrieved sources (proxy for "found the right passage")
    taxonomy.coverage        — % of expected canonical clause types present
    taxonomy.inconsistency   — # of raw clause_type variants that collapse to the
                               same canonical type (a data-quality smell)
    faithfulness.valid_rate  — % of answer citations that verify against sources
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import app.models  # noqa: F401  (registers all ORM models)
from sqlalchemy import select

from app.auth.models import User
from app.contract_brain.clause_taxonomy import canonical_clause_type as _canonicalize
from app.contract_brain.models import ClauseExtraction
from app.contract_brain.retrieval import (
    hybrid_sources,
    resolve_scope_contract_ids,
    sources_to_context,
)
from app.core.database import SessionLocal

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text())


def _sources_blob(sources: dict) -> str:
    parts = [s["text"] for s in sources.get("semantic", [])]
    parts += [c["excerpt"] for c in sources.get("clauses", [])]
    for t in sources.get("text", []):
        parts += [m["excerpt"] for m in t.get("matches", [])]
    return " ".join(parts).lower()


def _count_sources(sources: dict) -> int:
    return (
        len(sources.get("semantic", []))
        + len(sources.get("clauses", []))
        + len(sources.get("text", []))
    )


def run_retrieval(db, user) -> dict:
    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope="portfolio", contract_id=None, project_id=None
    )
    hits, kw_hits, rows = 0, 0, []
    for q in GOLDEN["brain_queries"]:
        sources = hybrid_sources(
            db, org_id=user.org_id, contract_ids=contract_ids, question=q["question"]
        )
        n = _count_sources(sources)
        blob = _sources_blob(sources)
        got_kw = any(k.lower() in blob for k in q["expect_keywords"])
        has = n >= q.get("min_sources", 1)
        hits += has
        kw_hits += got_kw
        rows.append((q["id"], n, has, got_kw))
    total = len(GOLDEN["brain_queries"])
    return {
        "total": total,
        "hit_rate": hits / total if total else 0,
        "keyword_recall": kw_hits / total if total else 0,
        "rows": rows,
    }


def run_taxonomy(db, user) -> dict:
    raw = list(
        db.scalars(
            select(ClauseExtraction.clause_type).where(
                ClauseExtraction.org_id == user.org_id,
                ClauseExtraction.is_stale.is_(False),
            )
        ).all()
    )
    canon_to_raw: dict[str, set[str]] = {}
    for ct in raw:
        canon_to_raw.setdefault(_canonicalize(ct), set()).add(ct)
    present = set(canon_to_raw)
    expected = GOLDEN["extraction_taxonomy"]["expected_canonical"]
    covered = [e for e in expected if e in present]
    inconsistent = {c: sorted(v) for c, v in canon_to_raw.items() if len(v) > 1}
    return {
        "expected": expected,
        "covered": covered,
        "coverage": len(covered) / len(expected) if expected else 0,
        "inconsistent": inconsistent,
    }


async def run_faithfulness(db, user) -> dict:
    from app.ai.citations import align_citation_to_source
    from app.ai.controller import ai_controller
    from app.ai.schemas import BrainAnswerOutput

    contract_ids = resolve_scope_contract_ids(
        db, user=user, scope="portfolio", contract_id=None, project_id=None
    )
    total_cites, valid_cites, rows = 0, 0, []
    for q in GOLDEN["brain_queries"]:
        sources = hybrid_sources(
            db, org_id=user.org_id, contract_ids=contract_ids, question=q["question"]
        )
        source_text = sources_to_context(sources)
        try:
            ans = await ai_controller.run_structured_skill(
                db,
                skill_name="contract_brain_answer",
                org_id=user.org_id,
                created_by_user_id=user.id,
                input_payload={
                    "question": q["question"],
                    "retrieved_context": source_text,
                    "scope": "portfolio",
                },
            )
            ans = ans if isinstance(ans, BrainAnswerOutput) else BrainAnswerOutput.model_validate(ans)
        except Exception as e:  # noqa: BLE001
            rows.append((q["id"], "ERROR", str(e)[:40]))
            continue
        n_valid = 0
        for c in ans.citations:
            total_cites += 1
            _span, score = align_citation_to_source(c.quote, source_text)
            if score >= 85.0:
                valid_cites += 1
                n_valid += 1
        rows.append((q["id"], ans.confidence, f"{n_valid}/{len(ans.citations)} cites valid"))
    return {
        "valid_rate": valid_cites / total_cites if total_cites else 0,
        "total_cites": total_cites,
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--faithfulness", action="store_true", help="also generate answers + verify citations (costs tokens)")
    ap.add_argument("--email", default="admin@example.com")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == args.email))
        if user is None:
            print(f"No user {args.email}", file=sys.stderr)
            sys.exit(1)

        print("=" * 62)
        print(" AEGIS AI QUALITY SCORECARD")
        print("=" * 62)

        r = run_retrieval(db, user)
        print(f"\nRETRIEVAL  ({r['total']} golden queries)")
        print(f"  hit_rate        {r['hit_rate']*100:5.1f}%   (>=1 source returned)")
        print(f"  keyword_recall  {r['keyword_recall']*100:5.1f}%   (expected passage retrieved)")
        for qid, n, has, kw in r["rows"]:
            print(f"    {'ok ' if kw else 'MISS'} {qid:22} sources={n:2}  kw={'Y' if kw else 'n'}")

        t = run_taxonomy(db, user)
        print(f"\nEXTRACTION TAXONOMY")
        print(f"  coverage        {t['coverage']*100:5.1f}%   ({len(t['covered'])}/{len(t['expected'])} canonical types present)")
        missing = [e for e in t["expected"] if e not in t["covered"]]
        if missing:
            print(f"    missing: {', '.join(missing)}")
        if t["inconsistent"]:
            print(f"  inconsistency   {len(t['inconsistent'])} canonical types have >1 raw label:")
            for c, variants in t["inconsistent"].items():
                print(f"    {c}: {variants}")
        else:
            print("  inconsistency   none — clause types are normalized")

        if args.faithfulness:
            print(f"\nFAITHFULNESS  (generates answers — costs tokens)")
            f = asyncio.run(run_faithfulness(db, user))
            print(f"  valid_rate      {f['valid_rate']*100:5.1f}%   ({f['total_cites']} citations checked)")
            for qid, conf, detail in f["rows"]:
                print(f"    {qid:22} conf={conf:6} {detail}")

        print("\n" + "=" * 62)
    finally:
        db.close()


if __name__ == "__main__":
    main()
