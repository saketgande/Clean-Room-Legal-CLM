import logging
import time
from datetime import timedelta
from io import BytesIO

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.database import utcnow
from app.core.enums import TabularCellStatus
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.tabular_review.models import (
    TabularReview,
    TabularReviewCell,
    TabularReviewChat,
    TabularReviewColumn,
)

logger = logging.getLogger(__name__)

MAX_REVIEW_CONTRACTS = 100
MAX_REVIEW_COLUMNS = 50
MAX_REVIEW_CELLS = 1000

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


def _enforce_review_size(*, contract_count: int, column_count: int) -> None:
    cell_count = contract_count * column_count
    if contract_count > MAX_REVIEW_CONTRACTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Tabular reviews are limited to {MAX_REVIEW_CONTRACTS} contracts",
        )
    if column_count > MAX_REVIEW_COLUMNS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Tabular reviews are limited to {MAX_REVIEW_COLUMNS} columns",
        )
    if cell_count > MAX_REVIEW_CELLS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Tabular reviews are limited to {MAX_REVIEW_CELLS} contract-column cells",
        )


class TabularReviewService:
    def __init__(self, db: Session):
        self.db = db

    def dispatch_cells(
        self,
        *,
        user: User,
        review: TabularReview,
        cells: list[TabularReviewCell],
        suffix: str = "",
    ) -> None:
        dispatch_cells(self.db, user=user, review=review, cells=cells, suffix=suffix)

    def build_table_context(self, *, review: TabularReview, org_id: str) -> str:
        return build_table_context(self.db, review=review, org_id=org_id)

    def build_xlsx(self, *, review: TabularReview, org_id: str) -> bytes:
        return build_xlsx(self.db, review=review, org_id=org_id)

    def review_payload(self, *, review: TabularReview, org_id: str) -> dict:
        db = self.db
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
        return {"review": review, "columns": columns, "cells": cells}

    def create_review(self, *, payload, current_user: User) -> TabularReview:
        db = self.db
        contract_ids = list(dict.fromkeys(payload.contract_ids))
        if not contract_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No contracts selected")
        # Access-check every row contract.
        for cid in contract_ids:
            get_contract_for_user(db, contract_id=cid, user=current_user)
        _enforce_review_size(contract_count=len(contract_ids), column_count=len(payload.columns))

        review = TabularReview(
            org_id=current_user.org_id,
            name=payload.name,
            source_contract_ids=contract_ids,
            status="running",
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(review)
        db.flush()
        columns = []
        for position, column_payload in enumerate(payload.columns):
            column = TabularReviewColumn(
                org_id=current_user.org_id,
                tabular_review_id=review.id,
                name=column_payload.name,
                prompt=column_payload.prompt,
                position=position,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
            db.add(column)
            db.flush()
            columns.append(column)
        cells = []
        for contract_id in contract_ids:
            for column in columns:
                cell = TabularReviewCell(
                    org_id=current_user.org_id,
                    tabular_review_id=review.id,
                    column_id=column.id,
                    contract_id=contract_id,
                    status=TabularCellStatus.PENDING,
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
                db.add(cell)
                db.flush()
                cells.append(cell)
        db.commit()
        self.dispatch_cells(user=current_user, review=review, cells=cells)
        db.refresh(review)
        return review

    def add_columns(self, *, review: TabularReview, payload, current_user: User) -> dict:
        """Add columns to an existing review and back-fill only the new cells.

        Existing answers are never re-run — only the new (contract x column)
        cells are created and dispatched, mirroring the reference behaviour.
        """
        db = self.db
        contract_ids: list[str] = list(review.source_contract_ids or [])
        for cid in contract_ids:
            get_contract_for_user(db, contract_id=cid, user=current_user)
        existing_column_count = db.scalar(
            select(func.count()).select_from(TabularReviewColumn).where(
                TabularReviewColumn.tabular_review_id == review.id
            )
        ) or 0
        _enforce_review_size(
            contract_count=len(contract_ids),
            column_count=existing_column_count + len(payload.columns),
        )
        next_pos = db.scalar(
            select(func.max(TabularReviewColumn.position)).where(
                TabularReviewColumn.tabular_review_id == review.id
            )
        )
        next_pos = (next_pos + 1) if next_pos is not None else 0

        new_columns = []
        for offset, col in enumerate(payload.columns):
            column = TabularReviewColumn(
                org_id=current_user.org_id,
                tabular_review_id=review.id,
                name=col.name,
                prompt=col.prompt,
                position=next_pos + offset,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
            db.add(column)
            db.flush()
            new_columns.append(column)

        cells = []
        for contract_id in contract_ids:
            for column in new_columns:
                cell = TabularReviewCell(
                    org_id=current_user.org_id,
                    tabular_review_id=review.id,
                    column_id=column.id,
                    contract_id=contract_id,
                    status=TabularCellStatus.PENDING,
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
                db.add(cell)
                db.flush()
                cells.append(cell)
        if cells:
            review.status = "running"
        review.updated_by_user_id = current_user.id
        db.commit()
        if cells:
            self.dispatch_cells(user=current_user, review=review, cells=cells)
        db.refresh(review)
        return self.review_payload(review=review, org_id=current_user.org_id)

    def add_contracts(self, *, review: TabularReview, payload, current_user: User) -> dict:
        """Add contracts (files) to an existing review and back-fill new cells."""
        db = self.db
        existing = list(review.source_contract_ids or [])
        existing_set = set(existing)
        new_contract_ids = [
            cid
            for cid in dict.fromkeys(payload.contract_ids)
            if cid not in existing_set
        ]
        if not new_contract_ids:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Those contracts are already in this review",
            )
        for cid in new_contract_ids:
            get_contract_for_user(db, contract_id=cid, user=current_user)

        columns = db.scalars(
            select(TabularReviewColumn)
            .where(TabularReviewColumn.tabular_review_id == review.id)
            .order_by(TabularReviewColumn.position.asc())
        ).all()
        _enforce_review_size(
            contract_count=len(existing) + len(new_contract_ids),
            column_count=len(columns),
        )

        cells = []
        for contract_id in new_contract_ids:
            for column in columns:
                cell = TabularReviewCell(
                    org_id=current_user.org_id,
                    tabular_review_id=review.id,
                    column_id=column.id,
                    contract_id=contract_id,
                    status=TabularCellStatus.PENDING,
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
                db.add(cell)
                db.flush()
                cells.append(cell)

        review.source_contract_ids = existing + new_contract_ids
        if cells:
            review.status = "running"
        review.updated_by_user_id = current_user.id
        db.commit()
        if cells:
            self.dispatch_cells(user=current_user, review=review, cells=cells)
        db.refresh(review)
        return self.review_payload(review=review, org_id=current_user.org_id)

    def rerun_cell(self, *, review: TabularReview, cell_id: str, current_user: User) -> TabularReviewCell:
        db = self.db
        cell = db.get(TabularReviewCell, cell_id)
        if (
            cell is None
            or cell.org_id != current_user.org_id
            or cell.tabular_review_id != review.id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tabular cell not found")
        get_contract_for_user(db, contract_id=cell.contract_id, user=current_user)
        cell.status = TabularCellStatus.PENDING
        cell.answer = None
        cell.reasoning = None
        cell.citations = None
        cell.error_message = None
        cell.updated_by_user_id = current_user.id
        # Re-open the parent run so it tracks the re-running cell again
        # (committing bumps updated_at, restarting the stuck-run TTL window).
        review.status = "running"
        review.updated_by_user_id = current_user.id
        db.commit()
        suffix = f":rerun:{int(time.time() * 1000)}"
        self.dispatch_cells(user=current_user, review=review, cells=[cell], suffix=suffix)
        db.refresh(cell)
        return cell

    def list_chat(self, *, review: TabularReview, current_user: User) -> list[TabularReviewChat]:
        return self.db.scalars(
            select(TabularReviewChat)
            .where(
                TabularReviewChat.org_id == current_user.org_id,
                TabularReviewChat.tabular_review_id == review.id,
            )
            .order_by(TabularReviewChat.created_at.asc())
        ).all()

    async def chat_over_table(
        self, *, review: TabularReview, message: str, current_user: User, request_id: str | None
    ) -> TabularReviewChat:
        from app.ai.citations import validate_citation
        from app.ai.controller import ai_controller
        from app.ai.schemas import CitationInput, TabularChatOutput

        db = self.db
        context_text = self.build_table_context(review=review, org_id=current_user.org_id)
        # Replay the recent conversation so a follow-up ("what about the second
        # one?") actually has memory — the chat rows were persisted but never fed
        # back to the model, so every question used to start cold.
        prior = db.scalars(
            select(TabularReviewChat)
            .where(
                TabularReviewChat.org_id == current_user.org_id,
                TabularReviewChat.tabular_review_id == review.id,
            )
            .order_by(TabularReviewChat.created_at.desc())
            .limit(8)
        ).all()
        convo = "\n".join(f"{h.role}: {(h.content or '')[:600]}" for h in reversed(prior))
        question = (
            f"Conversation so far:\n{convo}\n\nCurrent question: {message}"
            if convo
            else message
        )
        db.add(
            TabularReviewChat(
                org_id=current_user.org_id,
                tabular_review_id=review.id,
                role="user",
                content=message,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
        )
        db.commit()
        output = await ai_controller.run_structured_skill(
            db,
            skill_name="tabular_review_chat",
            org_id=current_user.org_id,
            created_by_user_id=current_user.id,
            input_payload={"question": question, "table_context": context_text},
            request_id=request_id,
        )
        answer = output if isinstance(output, TabularChatOutput) else TabularChatOutput.model_validate(output)
        validated = []
        for c in answer.citations:
            result = validate_citation(CitationInput(quote=c.quote), context_text or "")
            validated.append(
                {"quote": c.quote, "validation_status": result.validation_status,
                 "similarity_score": result.similarity_score}
            )
        chat_message = TabularReviewChat(
            org_id=current_user.org_id,
            tabular_review_id=review.id,
            role="assistant",
            content=answer.answer,
            citations=validated,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(chat_message)
        db.commit()
        db.refresh(chat_message)
        return chat_message


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
