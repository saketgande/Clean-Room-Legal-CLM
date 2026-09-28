import logging
from datetime import timedelta
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import utcnow
from app.core.enums import TabularCellStatus
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.tabular_review.models import (
    TabularReview,
    TabularReviewCell,
    TabularReviewColumn,
)

logger = logging.getLogger(__name__)

# A review whose cells have not all finished within this window is treated
# as stuck (e.g. a worker died) so it can resolve instead of showing
# "Running" forever.
STUCK_REVIEW_TTL = timedelta(minutes=20)
_TERMINAL_CELL = {
    TabularCellStatus.COMPLETE,
    TabularCellStatus.NEEDS_REVIEW,
    TabularCellStatus.FAILED,
}
ACTIVE_REVIEW_STATUSES = ("running", "pending", "draft")


# The 8,000-token output ceiling fits about 20 cited answers, so a wider review
# splits each row into a few calls.
_COLUMNS_PER_ROW_JOB = 20


def dispatch_cells(
    db: Session,
    *,
    user: User,
    review: TabularReview,
    cells: list[TabularReviewCell],
    suffix: str = "",
) -> None:
    """One job per contract row answers that row's columns in a single AI call, so
    each contract is read once rather than once per column. A lone cell (such as a
    re-run) keeps its own job. A failed job only fails its own cells."""
    rows: dict[str, list[TabularReviewCell]] = {}
    for cell in cells:
        rows.setdefault(cell.contract_id, []).append(cell)
    jobs = []
    for contract_id, row in rows.items():
        for start in range(0, len(row), _COLUMNS_PER_ROW_JOB):
            chunk = row[start:start + _COLUMNS_PER_ROW_JOB]
            if len(chunk) == 1:
                cell = chunk[0]
                jobs.append(
                    create_job(
                        db,
                        org_id=user.org_id,
                        job_type="tabular_cell_extraction",
                        resource_type="tabular_cell",
                        resource_id=cell.id,
                        created_by_user_id=user.id,
                        idempotency_key=f"tabular_cell_extraction:{cell.id}{suffix}",
                        metadata={
                            "cell_id": cell.id,
                            "tabular_review_id": review.id,
                            "column_id": cell.column_id,
                            "contract_id": cell.contract_id,
                        },
                    )
                )
                continue
            jobs.append(
                create_job(
                    db,
                    org_id=user.org_id,
                    job_type="tabular_row_extraction",
                    resource_type="tabular_review",
                    resource_id=review.id,
                    created_by_user_id=user.id,
                    idempotency_key=f"tabular_row_extraction:{chunk[0].id}{suffix}",
                    metadata={
                        "cell_ids": [c.id for c in chunk],
                        "tabular_review_id": review.id,
                        "contract_id": contract_id,
                    },
                )
            )
    db.flush()
    job_ids = [job.id for job in jobs]
    db.commit()
    for job_id in job_ids:
        job = db.get(JobRun, job_id)
        if job is not None:
            try:
                dispatch_job(db, job=job)
            except Exception:
                logger.warning("failed to dispatch job %s", job_id, exc_info=True)
    db.commit()


def build_table_context(db: Session, *, review: TabularReview, org_id: str) -> str:
    columns = db.scalars(
        select(TabularReviewColumn)
        .where(TabularReviewColumn.tabular_review_id == review.id)
        .order_by(TabularReviewColumn.position.asc())
    ).all()
    col_by_id = {c.id: c for c in columns}
    cells = db.scalars(
        select(TabularReviewCell).where(
            TabularReviewCell.org_id == org_id,
            TabularReviewCell.tabular_review_id == review.id,
        )
    ).all()
    lines: list[str] = []
    # Column definitions first, so the chat can answer meta-questions like
    # "what does the Direction column mean?" — it now sees each column's
    # defining prompt, not just the extracted cell values.
    if columns:
        lines.append("COLUMN DEFINITIONS (what each column asks of every contract):")
        for col in columns:
            q = (col.prompt or "").strip()
            lines.append(f"- {col.name}: {q}" if q else f"- {col.name}")
        lines.append("")
        lines.append("EXTRACTED CELLS:")
    for cell in cells:
        if cell.status not in {"complete", "needs_review"} or not cell.answer:
            continue
        contract = db.get(Contract, cell.contract_id)
        column = col_by_id.get(cell.column_id)
        lines.append(
            f"[contract: {contract.title if contract else cell.contract_id}]"
            f"[column: {column.name if column else cell.column_id}] {cell.answer}"
        )
    return "\n".join(lines)


def _xlsx_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def build_xlsx(db: Session, *, review: TabularReview, org_id: str) -> bytes:
    from openpyxl import Workbook

    columns = db.scalars(
        select(TabularReviewColumn)
        .where(TabularReviewColumn.tabular_review_id == review.id)
        .order_by(TabularReviewColumn.position.asc())
    ).all()
    cells = db.scalars(
        select(TabularReviewCell).where(
            TabularReviewCell.org_id == org_id,
            TabularReviewCell.tabular_review_id == review.id,
        )
    ).all()
    grid: dict[str, dict[str, TabularReviewCell]] = {}
    for cell in cells:
        grid.setdefault(cell.contract_id, {})[cell.column_id] = cell

    wb = Workbook()
    ws = wb.active
    ws.title = "Tabular Review"
    header = ["Contract"]
    for col in columns:
        header += [col.name, f"{col.name} — confidence", f"{col.name} — citations"]
    ws.append(header)
    for contract_id, row_cells in grid.items():
        contract = db.get(Contract, contract_id)
        row = [_xlsx_safe(contract.title if contract else contract_id)]
        for col in columns:
            cell = row_cells.get(col.id)
            if cell is None:
                row += ["", "", ""]
                continue
            row += [
                _xlsx_safe(cell.answer or ("(not found)" if cell.status == "complete" else f"({cell.status})")),
                cell.confidence or "",
                str(len(cell.citations or [])),
            ]
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def reconcile_review_status(db: Session, *, review: TabularReview) -> bool:
    """Derive a review's status from its cells, and fail cells stuck past the
    window so a review whose worker died can resolve and its cells be re-run.

    Runs when a cell job finishes and on a schedule, never from a read request.
    Returns True if it changed anything (the caller commits).
    """
    if review.status not in ACTIVE_REVIEW_STATUSES:
        return False
    cells = db.scalars(
        select(TabularReviewCell).where(
            TabularReviewCell.org_id == review.org_id,
            TabularReviewCell.tabular_review_id == review.id,
        )
    ).all()
    if not cells:
        return False
    pending = [c for c in cells if c.status not in _TERMINAL_CELL]
    changed = False
    if pending:
        last_active = review.updated_at or review.created_at
        if utcnow() - last_active < STUCK_REVIEW_TTL:
            if review.status != "running":
                review.status = "running"
                return True
            return False
        for cell in pending:
            cell.status = TabularCellStatus.FAILED
            cell.error_message = (
                "Timed out — no result within the expected window "
                "(the worker may have stopped). Re-run this cell to retry."
            )
        changed = True
    answered = any(
        c.status in {TabularCellStatus.COMPLETE, TabularCellStatus.NEEDS_REVIEW}
        for c in cells
    )
    new_status = "completed" if answered else "failed"
    if review.status != new_status:
        review.status = new_status
        changed = True
    return changed
