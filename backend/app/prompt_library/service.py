from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit_log
from app.core.enums import Visibility, WorkflowType
from app.core.models import AuditLog
from app.prompt_library.builtin import builtin_prompts
from app.prompt_library.models import Prompt, PromptVersion

# Visibilities a user may set on their own prompt (system_builtin is reserved).
_EDITABLE_VISIBILITIES = {
    Visibility.PRIVATE,
    Visibility.SHARED_WITH_USERS,
    Visibility.ORG_WIDE,
}
_LAUNCH_ACTION = "workflow.launched"


def _can_see(workflow: Prompt, user_id: str) -> bool:
    """Visibility gate for a custom (DB) workflow."""
    if workflow.created_by_user_id == user_id:
        return True
    if workflow.visibility == Visibility.ORG_WIDE:
        return True
    if workflow.visibility == Visibility.SHARED_WITH_USERS:
        return user_id in (workflow.shared_user_ids or [])
    return False


def _serialize_version(v: PromptVersion) -> dict[str, Any]:
    return {
        "id": v.id,
        "version_number": v.version_number,
        "name": v.name,
        "description": v.description,
        "definition": v.definition,
        "visibility": v.visibility,
        "note": v.note,
        "edited_by_user_id": v.created_by_user_id,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


class PromptLibraryService:
    def __init__(self, db: Session):
        self.db = db

    def _load_owned_workflow(self, workflow_id: str, current_user) -> Prompt:
        """Load a custom workflow the current user is allowed to modify. Built-ins
        are not in the DB, so they naturally 404 here (they are read-only)."""
        workflow = self.db.get(Prompt, workflow_id)
        if (
            workflow is None
            or workflow.deleted_at is not None
            or workflow.org_id != current_user.org_id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt not found")
        if workflow.created_by_user_id != current_user.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only the prompt's owner can edit it"
            )
        return workflow

    def _next_version_number(self, workflow_id: str) -> int:
        current_max = self.db.scalar(
            select(func.max(PromptVersion.version_number)).where(
                PromptVersion.workflow_id == workflow_id
            )
        )
        return int(current_max or 0) + 1

    def _snapshot(self, workflow: Prompt, *, actor_user_id: str, note: str | None = None) -> None:
        """Record the workflow's current (about-to-be-replaced) state as a version."""
        self.db.add(
            PromptVersion(
                org_id=workflow.org_id,
                workflow_id=workflow.id,
                version_number=self._next_version_number(workflow.id),
                name=workflow.name,
                description=workflow.description,
                definition=workflow.definition or {},
                visibility=workflow.visibility,
                note=note,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
        )

    def list_workflows(self, *, current_user) -> list:
        db = self.db
        rows = db.scalars(
            select(Prompt).where(
                Prompt.org_id == current_user.org_id, Prompt.deleted_at.is_(None)
            )
        ).all()
        # Enforce per-prompt visibility, then append the read-only built-ins that
        # every org sees.
        visible = [w for w in rows if _can_see(w, current_user.id)]
        return [*visible, *builtin_prompts()]

    def workflow_analytics(self, *, current_user) -> dict:
        """Per-prompt usage rollup, keyed by workflow id. Sourced from the audit
        log so it covers both custom and built-in prompts."""
        rows = self.db.execute(
            select(
                AuditLog.resource_id,
                func.count(AuditLog.id),
                func.max(AuditLog.created_at),
                func.count(func.distinct(AuditLog.actor_user_id)),
            )
            .where(
                AuditLog.org_id == current_user.org_id,
                AuditLog.action == _LAUNCH_ACTION,
                AuditLog.resource_type == "workflow",
                AuditLog.resource_id.is_not(None),
            )
            .group_by(AuditLog.resource_id)
        ).all()
        return {
            rid: {
                "run_count": int(count or 0),
                "last_run_at": last.isoformat() if last else None,
                "distinct_users": int(users or 0),
            }
            for rid, count, last, users in rows
        }

    def create_workflow(self, *, payload, current_user) -> Prompt:
        db = self.db
        if payload.visibility == Visibility.SYSTEM_BUILTIN:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "system_builtin workflows are reserved for built-in templates",
            )
        if payload.workflow_type not in {item.value for item in WorkflowType}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported workflow type")
        if payload.visibility not in _EDITABLE_VISIBILITIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported workflow visibility")
        workflow = Prompt(
            org_id=current_user.org_id,
            name=payload.name,
            workflow_type=payload.workflow_type,
            visibility=payload.visibility,
            description=payload.description,
            definition=payload.definition,
            shared_user_ids=[],
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
        return workflow

    def update_workflow(self, *, workflow_id: str, payload, current_user) -> Prompt:
        db = self.db
        workflow = self._load_owned_workflow(workflow_id, current_user)
        if payload.visibility is not None and payload.visibility not in _EDITABLE_VISIBILITIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported workflow visibility")

        before = {
            "name": workflow.name,
            "description": workflow.description,
            "visibility": workflow.visibility,
        }
        # Snapshot the state being replaced so the edit is reversible.
        self._snapshot(workflow, actor_user_id=current_user.id, note=payload.note)

        if payload.name is not None and payload.name.strip():
            workflow.name = payload.name.strip()
        if payload.description is not None:
            workflow.description = payload.description or None
        if payload.definition is not None:
            workflow.definition = payload.definition
        if payload.visibility is not None:
            workflow.visibility = payload.visibility
        if payload.shared_user_ids is not None:
            workflow.shared_user_ids = payload.shared_user_ids
        workflow.updated_by_user_id = current_user.id
        db.flush()

        write_audit_log(
            db,
            action="workflow.updated",
            resource_type="workflow",
            resource_id=workflow.id,
            org_id=workflow.org_id,
            actor_user_id=current_user.id,
            before=before,
            after={
                "name": workflow.name,
                "description": workflow.description,
                "visibility": workflow.visibility,
            },
        )
        db.commit()
        db.refresh(workflow)
        return workflow

    def list_workflow_versions(self, *, workflow_id: str, current_user) -> list[dict]:
        db = self.db
        workflow = db.get(Prompt, workflow_id)
        if (
            workflow is None
            or workflow.deleted_at is not None
            or workflow.org_id != current_user.org_id
            or not _can_see(workflow, current_user.id)
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt not found")
        versions = db.scalars(
            select(PromptVersion)
            .where(PromptVersion.workflow_id == workflow_id)
            .order_by(PromptVersion.version_number.desc())
        ).all()
        return [_serialize_version(v) for v in versions]

    def revert_workflow_version(self, *, workflow_id: str, version_id: str, current_user) -> Prompt:
        db = self.db
        workflow = self._load_owned_workflow(workflow_id, current_user)
        version = db.get(PromptVersion, version_id)
        if version is None or version.workflow_id != workflow.id or version.org_id != workflow.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")

        # Snapshot the current state first (so a revert is itself reversible), then
        # restore the chosen version's content onto the live workflow.
        self._snapshot(
            workflow,
            actor_user_id=current_user.id,
            note=f"Replaced by revert to v{version.version_number}",
        )
        workflow.name = version.name
        workflow.description = version.description
        workflow.definition = version.definition or {}
        workflow.updated_by_user_id = current_user.id
        db.flush()

        write_audit_log(
            db,
            action="workflow.reverted",
            resource_type="workflow",
            resource_id=workflow.id,
            org_id=workflow.org_id,
            actor_user_id=current_user.id,
            metadata={"reverted_to_version": version.version_number},
        )
        db.commit()
        db.refresh(workflow)
        return workflow

    def launch_workflow(self, *, workflow_id: str, payload, current_user) -> None:
        """Best-effort usage ping recorded when a prompt is used. Works for both
        custom and built-in prompts (audit_log.resource_id is a free string)."""
        if len(workflow_id) > 36:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid workflow id")
        db = self.db
        write_audit_log(
            db,
            action=_LAUNCH_ACTION,
            resource_type="workflow",
            resource_id=workflow_id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            metadata={"mode": payload.mode, "contract_id": payload.contract_id},
        )
        db.commit()
