from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.citations import validate_citation
from app.ai.models import AICitation
from app.ai.schemas import CitationInput
from app.assistant.models import (
    AssistantContractHandle,
    AssistantMessage,
    AssistantRun,
    AssistantSession,
    AssistantToolCall,
)
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.service import get_contract_for_user
from app.matters.access import get_project_for_user


class AssistantService:
    def __init__(self, db: Session):
        self.db = db

    def get_session_for_user(self, *, session_id: str, current_user) -> AssistantSession:
        session = self.db.get(AssistantSession, session_id)
        if (
            session is None
            or session.org_id != current_user.org_id
            or session.created_by_user_id != current_user.id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
        return session

    def get_run_for_user(self, *, assistant_run_id: str, current_user) -> AssistantRun:
        run = self.db.get(AssistantRun, assistant_run_id)
        if run is None or run.org_id != current_user.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant run not found")
        self.get_session_for_user(session_id=run.session_id, current_user=current_user)
        return run

    def tool_calls_for_run(self, *, assistant_run_id: str, org_id: str) -> list[AssistantToolCall]:
        return self.db.scalars(
            select(AssistantToolCall)
            .where(AssistantToolCall.org_id == org_id, AssistantToolCall.assistant_run_id == assistant_run_id)
            .order_by(AssistantToolCall.created_at.asc())
        ).all()

    def ensure_contract_handle(
        self,
        *,
        session: AssistantSession,
        contract_id: str,
        current_user,
        requested_handle: str | None = None,
    ) -> AssistantContractHandle:
        db = self.db
        existing = db.scalar(
            select(AssistantContractHandle).where(
                AssistantContractHandle.org_id == current_user.org_id,
                AssistantContractHandle.session_id == session.id,
                AssistantContractHandle.contract_id == contract_id,
            )
        )
        if existing is not None:
            return existing
        if requested_handle:
            duplicate = db.scalar(
                select(AssistantContractHandle).where(
                    AssistantContractHandle.org_id == current_user.org_id,
                    AssistantContractHandle.session_id == session.id,
                    AssistantContractHandle.handle == requested_handle,
                )
            )
            if duplicate is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "Contract handle already exists")
            handle_value = requested_handle
        else:
            handle_count = len(
                db.scalars(
                    select(AssistantContractHandle).where(
                        AssistantContractHandle.org_id == current_user.org_id,
                        AssistantContractHandle.session_id == session.id,
                    )
                ).all()
            )
            handle_value = f"contract-{handle_count}"
        handle = AssistantContractHandle(
            org_id=current_user.org_id,
            session_id=session.id,
            contract_id=contract_id,
            handle=handle_value,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(handle)
        db.flush()
        return handle

    def validate_and_store_assistant_citations(
        self,
        *,
        org_id: str,
        assistant_run_id: str,
        current_user,
        raw_citations: list[dict],
    ) -> list[dict]:
        """Validate assistant citation excerpts against source contract text using the
        shared fuzzy validator, persist AICitation rows, and return enriched citations."""
        db = self.db
        source_cache: dict[str, tuple[str, str | None, bool]] = {}

        def _source_for(contract_id: str) -> tuple[str, str | None, bool] | None:
            if contract_id in source_cache:
                return source_cache[contract_id]
            try:
                contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
            except Exception:
                source_cache[contract_id] = None
                return None
            version = (
                db.get(ContractVersion, contract.current_authoritative_version_id)
                if contract.current_authoritative_version_id
                else None
            )
            snapshot = (
                db.get(ContractTextSnapshot, version.text_snapshot_id)
                if version and version.text_snapshot_id
                else None
            )
            entry = (
                (snapshot.text or "", snapshot.id, bool(snapshot.ocr_provider))
                if snapshot is not None
                else ("", None, False)
            )
            source_cache[contract_id] = entry
            return entry

        enriched: list[dict] = []
        for citation in raw_citations:
            quote = citation.get("excerpt")
            contract_id = citation.get("contract_id")
            if not quote or not contract_id:
                enriched.append({**citation, "validation_status": "not_applicable"})
                continue
            source = _source_for(contract_id)
            if not source or not source[0]:
                enriched.append({**citation, "validation_status": "unverified"})
                continue
            source_text, snapshot_id, is_ocr = source
            result = validate_citation(
                CitationInput(quote=quote),
                source_text,
                is_ocr=is_ocr,
            )
            db.add(
                AICitation(
                    org_id=org_id,
                    assistant_run_id=assistant_run_id,
                    resource_type="contract",
                    resource_id=contract_id,
                    contract_id=contract_id,
                    text_snapshot_id=snapshot_id,
                    quote=quote,
                    normalized_quote=result.normalized_quote,
                    start_char=citation.get("start_char"),
                    end_char=citation.get("end_char"),
                    validation_status=result.validation_status,
                    similarity_score=result.similarity_score,
                    metadata_json={"message": result.message, "source": "assistant_tool_result"},
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
            )
            enriched.append(
                {
                    **citation,
                    "validation_status": result.validation_status,
                    "similarity_score": result.similarity_score,
                }
            )
        return enriched

    def persist_assistant_answer(
        self,
        *,
        org_id: str,
        session_id: str,
        run: AssistantRun,
        answer_parts: list[str],
        citations: list[dict],
        blocks: list[dict],
        current_user,
        extra_metadata: dict | None = None,
    ) -> None:
        """Persist whatever answer text was generated as an AssistantMessage, linking
        it to the run and any tool calls made during it.

        Called on success AND on interruption/failure: a stream that gets cut off
        after Claude already generated a real, useful partial answer must not throw
        that content away — the previous behavior silently discarded answer_parts
        whenever client_disconnected fired, leaving the user's question in history
        with no reply and no way to tell whether the assistant had said anything at
        all. Does not commit or touch run.status — callers set those to reflect
        why persistence happened (success vs. interrupted vs. failed).
        """
        db = self.db
        answer = "".join(answer_parts)
        if not answer:
            return
        citations = self.validate_and_store_assistant_citations(
            org_id=org_id,
            assistant_run_id=run.id,
            current_user=current_user,
            raw_citations=citations,
        )
        assistant_message = AssistantMessage(
            org_id=org_id,
            session_id=session_id,
            role="assistant",
            content=answer,
            citations=citations,
            metadata_json={
                "assistant_run_id": run.id,
                "blocks": blocks,
                **(extra_metadata or {}),
            },
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(assistant_message)
        db.flush()
        run.assistant_message_id = assistant_message.id
        for call in db.scalars(
            select(AssistantToolCall).where(
                AssistantToolCall.org_id == org_id,
                AssistantToolCall.assistant_run_id == run.id,
                AssistantToolCall.message_id.is_(None),
            )
        ):
            call.message_id = assistant_message.id

    # --- CRUD -------------------------------------------------------------

    def list_sessions(
        self,
        *,
        current_user,
        matter_id: str | None = None,
        contract_id: str | None = None,
        status_filter: str = "active",
        q: str | None = None,
        limit: int = 50,
    ) -> list[AssistantSession]:
        db = self.db
        query = select(AssistantSession).where(
            AssistantSession.org_id == current_user.org_id,
            AssistantSession.created_by_user_id == current_user.id,
        )
        if status_filter:
            query = query.where(AssistantSession.status == status_filter)
        if matter_id:
            get_project_for_user(db, matter_id=matter_id, user=current_user)
            query = query.where(AssistantSession.matter_id == matter_id)
        if contract_id:
            get_contract_for_user(db, contract_id=contract_id, user=current_user)
            query = query.where(AssistantSession.contract_id == contract_id)
        if q and q.strip():
            # Title-only search would be useless here: many sessions share an
            # identical auto-generated title (e.g. every "Edit · <contract>"
            # chat opened against the same contract), so also match on the
            # actual conversation content.
            needle = f"%{q.strip()}%"
            matching_session_ids = select(AssistantMessage.session_id).where(
                AssistantMessage.org_id == current_user.org_id,
                AssistantMessage.content.ilike(needle),
            )
            query = query.where(
                or_(AssistantSession.title.ilike(needle), AssistantSession.id.in_(matching_session_ids))
            )
        return db.scalars(query.order_by(AssistantSession.updated_at.desc()).limit(min(limit, 100))).all()

    def create_session(self, *, payload, current_user) -> AssistantSession:
        db = self.db
        if payload.matter_id:
            get_project_for_user(db, matter_id=payload.matter_id, user=current_user)
        if payload.contract_id:
            get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
        session = AssistantSession(
            org_id=current_user.org_id,
            session_type=payload.session_type,
            title=payload.title,
            matter_id=payload.matter_id,
            contract_id=payload.contract_id,
            tabular_review_id=payload.tabular_review_id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(session)
        db.flush()
        if payload.contract_id:
            db.add(
                AssistantContractHandle(
                    org_id=current_user.org_id,
                    session_id=session.id,
                    contract_id=payload.contract_id,
                    handle="contract-0",
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
            )
        db.commit()
        db.refresh(session)
        return session

    def get_session(self, *, session_id: str, current_user) -> dict:
        session = self.get_session_for_user(session_id=session_id, current_user=current_user)
        handles = self.db.scalars(
            select(AssistantContractHandle).where(AssistantContractHandle.session_id == session.id)
        ).all()
        return {"session": session, "contract_handles": handles}

    def update_session(self, *, session_id: str, payload, current_user) -> AssistantSession:
        db = self.db
        session = self.get_session_for_user(session_id=session_id, current_user=current_user)
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(session, key, value)
        session.updated_by_user_id = current_user.id
        db.commit()
        db.refresh(session)
        return session

    def add_contract_handle(self, *, session_id: str, payload, current_user) -> AssistantContractHandle:
        db = self.db
        session = self.get_session_for_user(session_id=session_id, current_user=current_user)
        get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
        handle = self.ensure_contract_handle(
            session=session,
            contract_id=payload.contract_id,
            current_user=current_user,
            requested_handle=payload.handle,
        )
        db.commit()
        db.refresh(handle)
        return handle

    def list_session_messages(self, *, session_id: str, limit: int, current_user) -> list[AssistantMessage]:
        session = self.get_session_for_user(session_id=session_id, current_user=current_user)
        return self.db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.org_id == current_user.org_id, AssistantMessage.session_id == session.id)
            .order_by(AssistantMessage.created_at.asc())
            .limit(min(limit, 200))
        ).all()

    def list_session_runs(self, *, session_id: str, limit: int, current_user) -> list[AssistantRun]:
        session = self.get_session_for_user(session_id=session_id, current_user=current_user)
        return self.db.scalars(
            select(AssistantRun)
            .where(AssistantRun.org_id == current_user.org_id, AssistantRun.session_id == session.id)
            .order_by(AssistantRun.created_at.desc())
            .limit(min(limit, 100))
        ).all()

    def get_run(self, *, assistant_run_id: str, current_user) -> dict:
        run = self.get_run_for_user(assistant_run_id=assistant_run_id, current_user=current_user)
        tool_calls = self.tool_calls_for_run(assistant_run_id=run.id, org_id=current_user.org_id)
        return {"assistant_run": run, "tool_calls": tool_calls}
