"""Seeds a real contract whose text matches golden.json's expectations
(confidentiality, term_and_termination, governing_law, remedies,
data_protection, limitation_of_liability, assignment — see golden.json's
extraction_taxonomy and brain_queries), then runs the REAL clause-extraction +
embeddings pipeline against it synchronously — so run_eval.py has genuine,
Claude-processed data to score instead of an empty database.

This is what makes the eval harness runnable in CI: a fresh Actions Postgres
starts with none of the organically-accumulated seed data that exists in a
long-running dev environment. Requires a real Claude API key (MOCK_CLAUDE must
be false) — this costs tokens (~2 calls: metadata + clause extraction; local
embeddings are free). Idempotent — skips if the fixture contract already
exists for this org.

Usage (inside the backend container, after `python -m app.devtools seed`):
    python eval/seed_golden_fixtures.py
"""

import asyncio
import io
import sys

from fastapi import UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

import app.models  # noqa: F401  (registers all ORM models)
from app.auth.models import User
from app.contract_files.service import create_contract_from_upload
from app.contracts.models import Contract
from app.core.database import SessionLocal
from app.jobs.models import JobRun
from app.jobs.tasks import _run_ai_job

FIXTURE_TITLE = "Golden Eval Fixture — Mutual NDA & DPA"

# One document deliberately covers every canonical clause type and keyword
# golden.json checks for, in clearly labeled sections — real contracts are
# structured this way, so this exercises clause_extraction the same way a real
# upload would rather than gaming the eval with artificial fragments.
CONTRACT_TEXT = """MUTUAL NON-DISCLOSURE AND DATA PROCESSING AGREEMENT

This Mutual Non-Disclosure and Data Processing Agreement (this "Agreement") is entered into by and between Aegis Legal Technologies, Inc. ("Company") and Golden Fixture Partners LLC ("Counterparty") (each a "Party" and together the "Parties").

1. DEFINITION OF CONFIDENTIAL INFORMATION
"Confidential Information" means any non-public information disclosed by either Party to the other, whether orally, in writing, or in any other form, that is designated as confidential or that a reasonable person would understand to be confidential given the nature of the information and the circumstances of disclosure. Confidential Information includes, without limitation, business plans, financial information, customer lists, technical data, and any personal data processed under this Agreement.

2. PERMITTED USE AND DISCLOSURE
Each Party may disclose the other Party's Confidential Information only to its employees, officers, and representatives who have a need to know such information for purposes of this Agreement and who are bound by confidentiality obligations at least as protective as those in this Agreement. A Party may also disclose Confidential Information to the extent required by law, regulation, or a valid order of a court or governmental authority, provided that the disclosing Party gives the other Party prompt written notice of such requirement where legally permissible.

3. TERM AND TERMINATION
This Agreement shall commence on the Effective Date and continue for a period of three (3) years, unless earlier terminated by either Party upon thirty (30) days' written notice to the other Party. The confidentiality obligations set forth in Sections 1 and 2 shall survive termination of this Agreement for a period of five (5) years following the date of termination.

4. RETURN OR DESTRUCTION OF CONFIDENTIAL INFORMATION
Upon termination of this Agreement or upon the disclosing Party's written request, the receiving Party shall promptly return or destroy all Confidential Information in its possession, including all copies, and shall certify such return or destruction in writing, except that the receiving Party may retain one copy solely for legal archival purposes and continued compliance with its confidentiality obligations.

5. DATA PROTECTION
To the extent either Party processes personal data on behalf of the other Party in connection with this Agreement, each Party shall implement appropriate technical and organizational measures to protect such personal data against unauthorized access, loss, or disclosure, and shall process personal data only in accordance with applicable data protection laws and the instructions of the disclosing Party.

6. REMEDIES
Each Party acknowledges that any breach of this Agreement may cause the other Party irreparable harm for which monetary damages would be an inadequate remedy. Accordingly, in addition to any other remedies available at law, the non-breaching Party shall be entitled to seek injunctive or other equitable relief to prevent or restrain any actual or threatened breach of this Agreement, without the necessity of posting a bond.

7. LIMITATION OF LIABILITY
Except for breaches of the confidentiality obligations in this Agreement, in no event shall either Party's total liability arising out of this Agreement exceed the amount of fees paid by one Party to the other in the twelve (12) months preceding the claim. Neither Party shall be liable for any indirect, incidental, or consequential damages.

8. ASSIGNMENT
Neither Party may assign or transfer this Agreement, in whole or in part, without the prior written consent of the other Party, except that either Party may assign this Agreement without consent to an affiliate or in connection with a merger, acquisition, or sale of all or substantially all of its assets.

9. GOVERNING LAW
This Agreement shall be governed by and construed in accordance with the laws of the State of Delaware, without regard to its conflict of laws principles. Each Party consents to the exclusive jurisdiction of the state and federal courts located in Delaware for any dispute arising out of or relating to this Agreement.
"""


async def _run_pending_jobs(db, *, contract_id: str) -> None:
    """Run the queued initial AI jobs synchronously, in dependency order —
    no Celery worker involved, so this is deterministic in CI."""
    order = {"metadata_extraction": 0, "clause_extraction": 1, "embeddings": 2}
    jobs = db.scalars(
        select(JobRun).where(JobRun.resource_type == "contract", JobRun.resource_id == contract_id)
    ).all()
    for job in sorted(jobs, key=lambda j: order.get(j.job_type, 99)):
        print(f"  running {job.job_type} ...")
        await _run_ai_job(job.id)


async def seed_golden_fixture() -> None:
    db = SessionLocal()
    try:
        existing = db.scalar(
            select(Contract).where(Contract.title == FIXTURE_TITLE, Contract.deleted_at.is_(None))
        )
        if existing is not None:
            print(f"Golden fixture already present: {existing.id}")
            return

        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        if user is None:
            print(
                "No admin@example.com user found — run `python -m app.devtools seed` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        data = CONTRACT_TEXT.encode("utf-8")
        upload = UploadFile(
            file=io.BytesIO(data),
            size=len(data),
            filename="golden-fixture-nda.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        result = await create_contract_from_upload(
            db,
            upload=upload,
            user=user,
            title=FIXTURE_TITLE,
            counterparty_name="Golden Fixture Partners LLC",
            contract_type="Mutual Non-Disclosure Agreement",
        )
        contract = result["contract"]
        db.commit()
        print(f"Created golden fixture contract {contract.id}; running AI jobs (this costs tokens)...")
        await _run_pending_jobs(db, contract_id=contract.id)
        print("Golden fixture seeded and processed.")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(seed_golden_fixture())
