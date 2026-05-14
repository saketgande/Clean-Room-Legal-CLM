from enum import StrEnum


class UserStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class ProjectType(StrEnum):
    GENERAL = "general"
    CONTRACT_REVIEW = "contract_review"
    DUE_DILIGENCE = "due_diligence"
    REGULATORY = "regulatory"


class ContractLifecycleStage(StrEnum):
    INTAKE = "intake"
    DRAFTING = "drafting"
    AI_REVIEW = "ai_review"
    INTERNAL_REVIEW = "internal_review"
    COUNTERPARTY_REVIEW = "counterparty_review"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    SIGNATURE_PENDING = "signature_pending"
    ACTIVE = "active"
    RENEWAL_DUE = "renewal_due"
    CLOSED = "closed"
    ARCHIVED = "archived"


class ContractVersionSource(StrEnum):
    UPLOAD = "upload"
    MANUAL_UPLOAD = "manual_upload"
    ASSISTANT_GENERATED = "assistant_generated"
    ASSISTANT_EDIT = "assistant_edit"
    PLAYBOOK_REDLINE = "playbook_redline"
    COUNTERPARTY_REVISION = "counterparty_revision"
    APPROVED_CLEAN = "approved_clean"
    SIGNED = "signed"
    RESTORED = "restored"
    TEMPLATE_GENERATED = "template_generated"


class AssistantSessionType(StrEnum):
    GENERAL = "general"
    PROJECT = "project"
    CONTRACT = "contract"
    TABULAR_REVIEW = "tabular_review"


class AssistantToolCategory(StrEnum):
    READ_ONLY = "read_only"
    DRAFT_OR_PROPOSE = "draft_or_propose"
    MUTATING = "mutating"
    EXTERNAL_ACTION = "external_action"
    DESTRUCTIVE = "destructive"


class WorkflowType(StrEnum):
    ASSISTANT = "assistant"
    TABULAR_REVIEW = "tabular_review"
    CONTRACT_REVIEW = "contract_review"
    DRAFTING = "drafting"
    INTAKE = "intake"


class Visibility(StrEnum):
    PRIVATE = "private"
    SHARED_WITH_USERS = "shared_with_users"
    ORG_WIDE = "org_wide"
    SYSTEM_BUILTIN = "system_builtin"


class PlaybookStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AIValidationStatus(StrEnum):
    NOT_VALIDATED = "not_validated"
    VALID = "valid"
    INVALID = "invalid"
    NEEDS_REVIEW = "needs_review"


class StorageBackend(StrEnum):
    LOCAL_VOLUME = "local_volume"


class ShareAccessMode(StrEnum):
    VIEW_ONLY = "view_only"
    DOWNLOAD_ALLOWED = "download_allowed"


class TabularCellStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class SignatureStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    DECLINED = "declined"
    VOIDED = "voided"


class ObligationStatus(StrEnum):
    OPEN = "open"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RenewalDecision(StrEnum):
    UNDECIDED = "undecided"
    RENEW = "renew"
    TERMINATE = "terminate"
    RENEGOTIATE = "renegotiate"
