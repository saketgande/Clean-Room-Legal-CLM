from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.tabular_review.models import (
    TabularReview,
    TabularReviewCell,
    TabularReviewColumn,
)


def dispatch_cells(
    db: Session,
    *,
    user: User,
    review: TabularReview,
    cells: list[TabularReviewCell],
    suffix: str = "",
) -> None:
    """One Celery job per cell — a failed cell never blocks the rest."""
    jobs = []
    for cell in cells:
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
    db.flush()
    job_ids = [job.id for job in jobs]
    db.commit()
    for job_id in job_ids:
        job = db.get(JobRun, job_id)
        if job is not None:
            try:
                dispatch_job(db, job=job)
            except Exception:
                pass
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
        row = [contract.title if contract else contract_id]
        for col in columns:
            cell = row_cells.get(col.id)
            if cell is None:
                row += ["", "", ""]
                continue
            row += [
                cell.answer or ("(not found)" if cell.status == "complete" else f"({cell.status})"),
                cell.confidence or "",
                str(len(cell.citations or [])),
            ]
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
