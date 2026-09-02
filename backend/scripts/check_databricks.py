#!/usr/bin/env python
"""Smoke-test Databricks document extraction against a real workspace.

Run it on ONE document before wiring anything into the app. It tells you, in
order, which of the five things that usually break is broken:

    1. credentials        — host / token / warehouse set?
    2. warehouse          — reachable, running, and SERVERLESS?
    3. Volume write       — can we PUT a file into the Volume?
    4. ai_parse_document  — does the whole text come back, with pages?
    5. ai_extract         — do the fields come back, and how sure is it?

Usage (inside the backend container):

    python scripts/check_databricks.py /path/to/contract.pdf
    python scripts/check_databricks.py /path/to/contract.pdf --no-precision
    python scripts/check_databricks.py /path/to/contract.pdf --text-only

Nothing is written to the Aegis database — this only reads a file and calls
Databricks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.integrations.databricks import CONTRACT_SCHEMA, databricks_client  # noqa: E402

OK, BAD, WARN = "  ✓", "  ✗", "  !"


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


def check_config() -> bool:
    step(1, "Credentials")
    rows = [
        ("DATABRICKS_HOST", settings.databricks_host),
        ("DATABRICKS_TOKEN", "set" if settings.databricks_token else None),
        ("DATABRICKS_WAREHOUSE_ID", settings.databricks_warehouse_id),
        ("DATABRICKS_VOLUME", settings.databricks_volume),
    ]
    ok = True
    for name, value in rows:
        print(f"{OK if value else BAD} {name}: {value or 'MISSING'}")
        if not value:
            ok = False
    print(f"{OK} precision mode: {settings.databricks_precision_mode}")
    if not ok:
        print("\n  Set these in backend/.env, then restart the container:")
        print("    DATABRICKS_HOST=https://<workspace>.cloud.databricks.com")
        print("    DATABRICKS_TOKEN=dapi...")
        print("    DATABRICKS_WAREHOUSE_ID=<serverless warehouse id>")
        print("    DATABRICKS_VOLUME=/Volumes/main/legal/contracts")
    return ok


async def check_warehouse() -> bool:
    step(2, "Warehouse")
    import httpx

    url = f"{settings.databricks_host}/api/2.0/sql/warehouses/{settings.databricks_warehouse_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {settings.databricks_token}"})
    except Exception as exc:
        print(f"{BAD} cannot reach {settings.databricks_host}: {exc}")
        return False

    if r.status_code == 403:
        print(f"{BAD} 403 — the token cannot see this warehouse (wrong workspace, or no permission)")
        return False
    if r.status_code == 404:
        print(f"{BAD} 404 — no warehouse with id {settings.databricks_warehouse_id}")
        return False
    if r.status_code != 200:
        print(f"{BAD} HTTP {r.status_code}: {r.text[:200]}")
        return False

    wh = r.json()
    kind = wh.get("warehouse_type"), wh.get("enable_serverless_compute")
    print(f"{OK} name: {wh.get('name')}  state: {wh.get('state')}")
    if not wh.get("enable_serverless_compute"):
        print(f"{BAD} NOT serverless ({kind}) — ai_parse_document/ai_extract cannot run on Pro or Classic")
        return False
    print(f"{OK} serverless: yes")
    if wh.get("state") != "RUNNING":
        print(f"{WARN} warehouse is {wh.get('state')} — the first query will pay a cold start")
    return True


async def run(path: Path, precision: bool, text_only: bool) -> int:
    print("=" * 72)
    print(f"Databricks extraction check — {path.name} ({path.stat().st_size / 1024:.0f} KB)")
    print("=" * 72)

    if not check_config():
        return 1
    if not await check_warehouse():
        return 1

    content = path.read_bytes()

    step(3, "Volume write + ai_parse_document")
    t0 = time.time()
    try:
        doc = await databricks_client.parse_document(filename=path.name, content=content)
    except Exception as exc:
        print(f"{BAD} {type(exc).__name__}: {exc}")
        print("\n  Common causes: Volume does not exist, token lacks WRITE VOLUME,")
        print("  runtime below 17.3, or the function is unavailable in this region.")
        return 1
    parse_secs = time.time() - t0

    if not doc.text:
        print(f"{BAD} no text came back after {parse_secs:.1f}s")
        if doc.errors:
            print(f"      parse errors: {json.dumps(doc.errors)[:300]}")
        return 1

    kinds: dict[str, int] = {}
    for el in doc.elements:
        kinds[el.get("type") or "?"] = kinds.get(el.get("type") or "?", 0) + 1

    print(f"{OK} parsed in {parse_secs:.1f}s")
    print(f"{OK} pages: {doc.page_count}   elements: {len(doc.elements)}   characters: {len(doc.text):,}")
    print(f"{OK} element types: {kinds}")
    print(f"{OK} tables found: {len(doc.tables())}")
    if doc.errors:
        print(f"{WARN} per-page errors: {json.dumps(doc.errors)[:200]}")

    print("\n  --- first 400 characters -------------------------------------")
    print("  " + doc.text[:400].replace("\n", "\n  "))
    print("  --------------------------------------------------------------")

    pages = doc.text_by_page()
    if pages:
        last = max(pages)
        print(f"\n  --- last page ({last}) first 200 chars -----------------------")
        print("  " + pages[last][:200].replace("\n", "\n  "))
        print("  --------------------------------------------------------------")
        print(f"{OK} page numbers present — citations will work")

    if text_only:
        print("\n--text-only: skipping field extraction.")
        return 0

    step(4, f"ai_extract ({'precision' if precision else 'default'} mode, {len(CONTRACT_SCHEMA)} fields)")
    t0 = time.time()
    try:
        result = await databricks_client.extract_fields(
            filename=path.name, content=content, precision=precision
        )
    except Exception as exc:
        print(f"{BAD} {type(exc).__name__}: {exc}")
        return 1
    extract_secs = time.time() - t0

    if result.error:
        print(f"{BAD} extraction error: {result.error}")
        return 1

    print(f"{OK} extracted in {extract_secs:.1f}s\n")
    width = max(len(k) for k in CONTRACT_SCHEMA)
    for key in CONTRACT_SCHEMA:
        value = result.fields.get(key)
        conf = result.confidence.get(key)
        cited = "cited" if key in result.citations else ""
        flag = OK if value not in (None, "") else WARN
        conf_txt = f"{conf:.2f}" if conf is not None else "  — "
        print(f"{flag} {key.ljust(width)}  {conf_txt}  {str(value)[:52]:<54}{cited}")

    review = result.needs_review()
    print()
    if review:
        print(f"{WARN} would go to human review (confidence < 0.70): {', '.join(review)}")
    else:
        print(f"{OK} every field above the review threshold")

    step(5, "Summary")
    print(f"{OK} parse {parse_secs:.1f}s + extract {extract_secs:.1f}s = {parse_secs + extract_secs:.1f}s per document")
    filled = sum(1 for k in CONTRACT_SCHEMA if result.fields.get(k) not in (None, ""))
    print(f"{OK} {filled}/{len(CONTRACT_SCHEMA)} fields filled")
    print("\n  Now check spend for this run in a Databricks SQL editor:")
    print("    SELECT usage_date, usage_metadata.ai_function, SUM(usage_quantity)")
    print("    FROM system.billing.usage")
    print("    WHERE billing_origin_product = 'AI_FUNCTIONS'")
    print("      AND usage_date >= current_date() - INTERVAL 1 DAYS")
    print("    GROUP BY 1,2;")
    print("\n  Then re-run with --no-precision and compare accuracy against cost.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("document", type=Path, help="a PDF/DOCX to test with — use a sample, not real signed paper")
    ap.add_argument("--no-precision", action="store_true", help="run ai_extract without precision mode")
    ap.add_argument("--text-only", action="store_true", help="parse only, skip field extraction")
    args = ap.parse_args()

    if not args.document.exists():
        print(f"No such file: {args.document}")
        return 1
    return asyncio.run(run(args.document, not args.no_precision, args.text_only))


if __name__ == "__main__":
    raise SystemExit(main())
