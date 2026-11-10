"""The deliberate, paid AI sample for spec 014 (FR-1A, AC-1A): do our own templates pass their playbooks?

Reads the org's CURRENT wording of each template, filled for the 19 main combinations
(``eval.template_matrix.MAIN``) x ``--runs`` (3), through the real AI playbook review
(``PlaybooksService.review_text``, the review a drafted contract gets). Then 7 risky counterparty-paper
texts (one per playbook: our default wording plus that playbook's prohibited wording) that MUST come
back with findings, proving the review can still see a problem.

    docker compose exec -e MOCK_CLAUDE=false backend \
        python -m eval.template_alignment --as admin@example.com [--runs 3] [--only nda,sow] [--yes]

Without ``--yes`` it prints the plan and cost estimate and spends nothing. Each main run is recorded in
the template's check history (trigger "sample"); the dated report goes to
``eval/results/template_alignment/``.

Pass rule: the model's findings vary run to run, so a combination passes unless the SAME finding repeats in
at least 2 of its runs. "Same finding" = same ``clause_type`` within the combination (``repeated_clause_types``).
# ponytail: clause_type is coarse; two different problems in one clause type count as one repeat. Upgrade:
# compare the cited clause number or the deviation text, which the model words differently each run.
Exit 1 if any combination has a repeated clause_type, any risky run has no findings, or any run errored.
Never run by CI (nothing in tests/ imports it).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path


def repeated_clause_types(runs: list[dict]) -> dict[str, list[str]]:
    """{combination: [clause_type seen in >= 2 of its runs]}, only combinations with a repeat.
    A clause type counts once per run, however many findings it has in that run."""
    seen: dict[str, dict[str, int]] = {}
    for r in runs:
        counts = seen.setdefault(r["combination"], {})
        for ct in {str(f.get("clause_type")) for f in r.get("findings") or []}:
            counts[ct] = counts.get(ct, 0) + 1
    return {c: sorted(ct for ct, n in counts.items() if n >= 2) for c, counts in seen.items()
            if any(n >= 2 for n in counts.values())}


COST_PER_REVIEW = 0.10  # dollars, a rough per-review estimate used only for the plan
RESULTS = Path(__file__).parent / "results" / "template_alignment"
DEV_ENVIRONMENTS = {"local", "development", "dev"}


_BREACH_72 = "Any personal data breach must be notified to the other Party within seventy-two (72) hours of discovery."
_FOREIGN_COURTS = ("Each Party irrevocably submits to the exclusive jurisdiction of the courts of Singapore for any "
                   "dispute arising out of this Agreement, which is governed by the laws of the State of New York.")
# One complete operative sentence per (template, clause type) that contains that rule's first prohibited
# phrase. The first AI sample appended bare fragments ("indemnify", "residuals") as a numbered list and the
# reviewer judged the list "a drafting artefact or test", finding nothing; a clause that reads like contract
# wording is what a counterparty's paper actually looks like. Descriptive prohibited text (the NDA
# definition rule) cannot appear verbatim, so its sentence does what the description says instead.
RISKY_SENTENCES: dict[tuple[str, str], str] = {
    ("nda", "definition_of_confidential_information"):
        "Confidential Information does not include AI-generated outputs, model training data or derivative works, "
        "and information disclosed orally is never Confidential Information, whether or not it is confirmed in "
        "writing.",
    ("nda", "permitted_use_and_standard_of_care"):
        "The Receiving Party may use the Confidential Information for any purpose whatsoever.",
    ("nda", "compelled_disclosure_and_legal_process"):
        "Any disclosure of Confidential Information required by law shall be deemed a waiver of all "
        "confidentiality obligations.",
    ("nda", "return_or_destruction_of_information"):
        "The Receiving Party may retain and use all Confidential Information for internal business purposes after "
        "this Agreement ends and is not required to return or destroy it.",
    ("nda", "term_and_duration_of_confidentiality"):
        "The Receiving Party's obligations under this Agreement shall expire upon termination of this Agreement.",
    ("nda", "governing_law_and_jurisdiction"):
        "Each Party irrevocably submits to the exclusive jurisdiction of the courts of the State of New York for "
        "any dispute arising out of this Agreement.",
    ("nda", "remedies_and_injunctive_relief"):
        "The Disclosing Party's remedies for any breach of this Agreement shall be limited to monetary damages, "
        "and no injunctive relief may be sought.",
    ("nda", "non_compete_and_exclusivity"):
        "For two years after this Agreement ends, the Receiving Party shall not compete with the Disclosing Party "
        "in any market in which the Disclosing Party operates.",
    ("nda", "indemnity"):
        "The Receiving Party shall indemnify and hold harmless the Disclosing Party against all losses arising "
        "from any use of the Confidential Information.",
    ("nda", "residuals"):
        "The Receiving Party may freely use residuals, meaning anything retained in the unaided memory of its "
        "personnel, for any purpose.",
    ("msa", "limitation_of_liability"):
        "The Customer shall have unlimited liability for every claim arising under this Agreement.",
    ("msa", "insurance"):
        "The Provider shall maintain insurance with limits of cover appropriate to its business, as it decides.",
    ("msa", "service_levels"):
        "Service credits are the Customer's sole and exclusive remedy for any failure to meet a service level.",
    ("msa", "data_protection"): _BREACH_72,
    ("msa", "governing_law"): _FOREIGN_COURTS,
    ("sow", "acceptance"):
        "Each Deliverable is deemed accepted if the Customer does not object within two days of delivery.",
    ("sow", "fees_and_pricing"):
        "The Provider's rates may be increased by the Provider at any time by written notice.",
    ("sow", "milestone_payments"):
        "The fee for every milestone is payable on signature of this SOW.",
    ("sow", "governing_law"):
        "This SOW is governed by the laws of Singapore and any dispute is to be heard in its courts.",
    ("vendor", "limitation_of_liability"):
        "The Company accepts unlimited liability for every claim arising under this Agreement.",
    ("vendor", "data_protection"): _BREACH_72,
    ("vendor", "delivery_and_acceptance"):
        "Goods and services are deemed accepted on delivery, whether or not the Company has inspected them.",
    ("vendor", "insurance"):
        "The Vendor shall maintain insurance with limits of cover appropriate to its own assessment of its business.",
    ("vendor", "governing_law"): _FOREIGN_COURTS,
    ("saas", "data_security"): _BREACH_72,
    ("saas", "data_location"):
        "The Provider may store and process Customer Data in any location chosen by the Provider.",
    ("saas", "limitation_of_liability"):
        "The Customer shall have unlimited liability for its use of the Service.",
    ("saas", "suspension"):
        "The Provider may suspend the Service at any time and without notice.",
    ("saas", "data_use"):
        "The Provider may use Customer Data to improve and train its own products and services.",
    ("saas", "governing_law"): _FOREIGN_COURTS,
    ("saas", "insurance"):
        "The Provider shall maintain insurance with limits of cover appropriate to its business, as it decides.",
    ("consultancy", "limitation_of_liability"):
        "The Company shall have unlimited liability for every claim arising from the Services.",
    ("consultancy", "governing_law"): _FOREIGN_COURTS,
    ("consultancy", "insurance"):
        "The Consultant shall maintain insurance with limits of cover appropriate to its business, as it decides.",
    ("consultancy", "data_protection"): _BREACH_72,
    ("consultancy", "non_compete"):
        "The Company's personnel shall not compete with the Consultant for three years after the Services end.",
    ("dpa", "breach_notification"):
        "The Processor shall notify the Controller of a Personal Data breach within seventy-two (72) hours of "
        "becoming aware of it.",
    ("dpa", "international_transfers"):
        "The Processor may transfer Personal Data to any country it chooses without notice to the Controller.",
    ("dpa", "return_or_deletion"):
        "When the services end the Processor may retain Personal Data for its own purposes.",
    ("dpa", "governing_law"):
        "This Agreement shall be governed by and construed in accordance with the laws of Singapore.",
}
_RISKY_INTRO = (
    "\n\nSPECIAL CONDITIONS\n\nThe following Special Conditions form part of this Agreement and prevail over any "
    "provision of it that is inconsistent with them.\n\n"
)


def _risky_text(key: str, base: str) -> tuple[str, list[str]]:
    """Counterparty paper: our default draft plus, for each rule with prohibited wording, one complete
    operative sentence containing it (``RISKY_SENTENCES``), set out as numbered Special Conditions that prevail
    over the rest. Returns the text and the clause types we expect flagged."""
    from app.drafting_templates.standard import STANDARD_RULES
    from app.playbooks.service import _phrases

    lines, types = [], []
    for r in STANDARD_RULES[key]:
        phrases = _phrases(r.get("prohibited_language"))
        if phrases:
            lines.append(RISKY_SENTENCES.get((key, r["clause_type"]), f"Notwithstanding anything else in this "
                                             f"Agreement: {phrases[0]}."))
            types.append(r["clause_type"])
    return base + _RISKY_INTRO + "\n\n".join(f"S{i}. {line}" for i, line in enumerate(lines, 1)), types


def _plan(only: list[str], runs: int):
    from eval.template_matrix import MAIN

    main = [c for c in MAIN if c[0].split("|")[0] in only]
    risky = sorted({c[0].split("|")[0] for c in MAIN if c[0].split("|")[0] in only})
    return main, risky


def print_plan(main, risky, runs: int) -> int:
    from app.core.config import settings

    total = len(main) * runs + len(risky)
    print(f"Template alignment sample: {len(main)} combinations x {runs} runs = {len(main) * runs} reviews, "
          f"+ {len(risky)} risky counterparty-paper runs = {total} reviews")
    for label, *_ in main:
        print(f"  {label}")
    print(f"Estimated cost: about ${total * COST_PER_REVIEW:.2f} (at ${COST_PER_REVIEW:.2f} per review)")
    problems = _refusals(settings)
    if problems:
        print("A paid run would be refused now:\n  " + "\n  ".join(problems))
    print("Nothing was run. Add --yes to spend it.")
    return total


def _refusals(settings) -> list[str]:
    out = []
    if settings.mock_claude:
        out.append("MOCK_CLAUDE is on: a mocked review proves nothing. Run with `-e MOCK_CLAUDE=false`.")
    if not settings.claude_api_key:
        out.append("No Claude API key is set.")
    if settings.environment.lower() not in DEV_ENVIRONMENTS:
        out.append(f"Environment is {settings.environment!r}; this sample runs only in local/development.")
    return out


async def _run(args, main, risky) -> int:
    from sqlalchemy import select

    import app.models  # noqa: F401  (registers every ORM model)
    from app.auth.models import User
    from app.core.audit import write_audit_log
    from app.core.database import SessionLocal, utcnow
    from app.drafting_templates import checks
    from app.drafting_templates.models import DraftingTemplateCheck
    from app.drafting_templates.service import current_wording, get_type, reviewing_playbook
    from app.playbooks.service import PlaybooksService
    from eval.template_matrix import render

    batch = str(uuid.uuid4())
    runs_out, risky_out, failures, failing = [], [], 0, {}
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.as_user))
        if user is None or not getattr(user, "is_active", True):
            print(f"--as {args.as_user!r} is not an active user.")
            return 2
        org_id, playbooks = user.org_id, PlaybooksService(db)

        def setup(key):
            ttype = get_type(db, org_id, key)
            reviewing = reviewing_playbook(db, org_id, ttype) if ttype.playbook_id else None
            if reviewing is None or not reviewing.available or reviewing.playbook is None:
                raise RuntimeError(f"{key}: no playbook reviews this template")
            return ttype, current_wording(db, org_id, ttype), reviewing.playbook

        async def review(ttype, playbook, text, rid, counterparty):
            return await playbooks.review_text(
                user=user, playbook=playbook, text=text, title=ttype.name, contract_type=ttype.contract_type,
                counterparty_name=counterparty, resource_type=checks.RESOURCE_TYPE, resource_id=rid)

        for label, fields, ctx, company, counterparty in main:
            key = label.split("|")[0]
            for n in range(1, args.runs + 1):
                started = utcnow()
                row = {"template": key, "combination": label, "run": n}
                try:
                    ttype, wording, playbook = setup(key)
                    text = render(key, fields, ctx, company=company, counterparty=counterparty, body=wording.body)
                    check = DraftingTemplateCheck(
                        org_id=org_id, created_by_user_id=user.id, template_key=key,
                        template_version=wording.version, wording_sha256=wording.sha256, trigger="sample",
                        trigger_detail={"batch": batch, "combination": label, "run": n, "fields": fields},
                        status="completed", started_at=started)
                    db.add(check)
                    db.flush()
                    result = await review(ttype, playbook, text, check.id, counterparty)
                    used = result["findings"]
                    for k, v in {
                        "playbook_id": result["playbook_id"], "playbook_name": result["playbook_name"],
                        "playbook_version_id": result["playbook_version_id"], "rule_count": result["rule_count"],
                        "findings": checks._enrich(used, checks.check_values(key)), "summary": result["summary"],
                        "text_was_truncated": bool(result["text_was_truncated"]), "text_length": len(text),
                        "read_length": len(text), "finished_at": utcnow(),
                    }.items():
                        setattr(check, k, v)
                    from app.playbooks.models import PlaybookVersion
                    pv = db.get(PlaybookVersion, result["playbook_version_id"])
                    check.playbook_version_number = pv.version_number if pv else None
                    write_audit_log(
                        db, action="drafting_template.check_finished", resource_type=checks.RESOURCE_TYPE,
                        resource_id=check.id, org_id=org_id, actor_user_id=user.id,
                        after={"check_id": check.id, "key": key, "trigger": "sample", "finding_count": len(used)})
                    db.commit()  # per run: no audit write is held across the next AI call
                    row |= {"finding_count": len(check.findings), "findings": check.findings}
                except Exception as exc:
                    db.rollback()
                    failures += 1
                    row |= {"error": f"{type(exc).__name__}: {exc}"}
                runs_out.append(row)
                print(f"  {label} run {n}: " + (row.get("error") or f"{row['finding_count']} findings"))
            repeated = repeated_clause_types([r for r in runs_out if r["combination"] == label]).get(label, [])
            if repeated:
                failing[label] = repeated
            print(f"  {label} verdict: " + (f"FAIL, repeated {', '.join(repeated)}" if repeated else "pass"))

        for key in risky:
            row = {"playbook": key}
            try:
                ttype, wording, playbook = setup(key)
                label, fields, ctx, company, counterparty = next(c for c in main if c[0].startswith(key + "|"))
                text, expected = _risky_text(key, render(key, fields, ctx, company=company,
                                                         counterparty=counterparty, body=wording.body))
                result = await review(ttype, playbook, text, str(uuid.uuid4()), counterparty)
                found = sorted({str(f["clause_type"]) for f in result["findings"]})
                row |= {"expected_clause_types": expected, "found_clause_types": found,
                        "finding_count": len(result["findings"])}
                if not result["findings"]:
                    failures += 1
            except Exception as exc:
                db.rollback()
                failures += 1
                row |= {"error": f"{type(exc).__name__}: {exc}"}
            risky_out.append(row)
            print(f"  risky {key}: " + (row.get("error") or f"{row['finding_count']} findings"))

        report = {
            "date": date.today().isoformat(), "batch": batch, "as": args.as_user, "runs_per_combination": args.runs,
            "runs": runs_out, "negatives": risky_out, "repeated": failing,
            "totals": {"main_runs": len(runs_out), "main_with_findings": sum(1 for r in runs_out if r.get("findings")),
                       "risky_runs": len(risky_out), "failures": failures,
                       "combinations_failing": len(failing)},
        }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{report['date']}-{batch[:8]}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    print(f"Report: {path}\nFailures: {failures}; combinations failing: {len(failing)}")
    return 1 if failures or failing else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="as_user", default="admin@example.com", help="active user who is accountable")
    ap.add_argument("--runs", type=int, default=3, help="reviews per main combination (default 3)")
    ap.add_argument("--only", help="comma-separated template keys, e.g. nda,sow")
    ap.add_argument("--yes", action="store_true", help="really spend: without it only the plan is printed")
    args = ap.parse_args()

    from eval.template_matrix import KEYS

    only = [k for k in (args.only.split(",") if args.only else KEYS) if k]
    bad = [k for k in only if k not in KEYS]
    if bad:
        ap.error(f"unknown template key(s): {', '.join(bad)}")
    main_set, risky = _plan(only, args.runs)
    if not args.yes:
        print_plan(main_set, risky, args.runs)
        return 0

    from app.core.config import settings

    problems = _refusals(settings)
    if problems:
        print("Refusing to run:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    return asyncio.run(_run(args, main_set, risky))


if __name__ == "__main__":
    sys.exit(main())
