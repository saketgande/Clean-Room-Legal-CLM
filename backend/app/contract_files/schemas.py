from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contract_files import redline_edits
from app.core.enums import ShareAccessMode


class ContractVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_file_id: str
    version_number: int
    storage_object_id: str
    text_snapshot_id: str | None
    source: str
    change_summary: str | None
    is_authoritative: bool
    created_at: datetime


class ContractFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    current_version_id: str | None
    file_label: str


class ContractTextSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_version_id: str
    extraction_method: str
    extraction_quality_score: float
    ocr_provider: str | None
    validation_status: str | None
    text: str = ""


class IssueFixJobResponse(BaseModel):
    """Progress of the job that drafts a suggested change for each playbook issue that has none.

    ``status`` is queued | running | succeeded | failed | cancelled. ``total`` is how many
    issues the job took on (at most the configured cap) and ``done`` how many it has finished;
    ``left_for_later`` is how many more were over the cap. ``drafted`` got a change,
    ``no_change`` is where the model found no wording to change, and ``failed`` hit an error.
    """

    job_id: str
    status: str
    trigger: str | None = None
    total: int
    done: int
    drafted: int
    no_change: int
    failed: int
    left_for_later: int
    stopped_early: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DraftIssueFixesResponse(BaseModel):
    """What asking for the fixes did. ``job`` is None when no issue needed one (``reason``
    says why: nothing_to_draft)."""

    job: IssueFixJobResponse | None = None
    reason: str | None = None


class ContractEditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_version_id: str
    edit_type: str
    status: str
    original_text: str | None
    replacement_text: str | None
    rationale: str | None
    citation: list | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def playbook_deviation_id(self) -> str | None:
        """The playbook issue this change fixes; deciding it also decides the issue."""
        return redline_edits.paired_deviation_id(self.citation)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def locatable(self) -> bool:
        """False when the change was never placed in the text, so it can only be rejected."""
        return redline_edits.is_locatable(self.edit_type, self.citation, self.replacement_text)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blank(self) -> str | None:
        """A fill-in blank this change would write into the contract ("[INSERT DATE]"), if any: it can
        only be rejected. A person's own typed change is theirs to word, so it is never flagged."""
        return redline_edits.blank_in(self.edit_type, self.replacement_text)


class ContractEditDecisionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class ContractShareCreate(BaseModel):
    contract_version_id: str | None = None
    access_mode: ShareAccessMode = ShareAccessMode.VIEW_ONLY
    expires_at: datetime | None = None
    # Minimum length 8: share passcodes gate unauthenticated external access,
    # so they must resist brute-force more than a 4-digit PIN would.
    passcode: str | None = Field(default=None, min_length=8, max_length=128)
    download_allowed: bool = False


class ContractShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_version_id: str | None
    access_mode: str
    expires_at: datetime | None
    revoked_at: datetime | None
    download_allowed: bool
    created_at: datetime


class ContractShareCreateResponse(BaseModel):
    share: ContractShareResponse
    token: str


class ExternalShareResponse(BaseModel):
    contract_id: str
    contract_version_id: str | None
    title: str
    filename: str | None = None
    access_mode: str
    download_allowed: bool
    text_excerpt: str | None = None
    text_truncated: bool = False
    can_submit: bool = False
    expires_at: datetime | None = None


class ExternalCommentResponse(BaseModel):
    id: str
    author_name: str
    author_kind: str  # user | counterparty
    body: str
    resolved: bool
    created_at: datetime


class ExternalCommentCreate(BaseModel):
    author_name: str | None = None
    body: str
