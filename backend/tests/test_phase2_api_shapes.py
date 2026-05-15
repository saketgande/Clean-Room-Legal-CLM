import pytest
from pydantic import ValidationError

from app.contract_files.routes import EXTERNAL_TEXT_EXCERPT_CHARS, _hash_secret
from app.contract_files.schemas import ContractShareCreate
from app.contract_files.service import _text_snapshot_validation_status
from app.core.enums import ShareAccessMode
from app.integrations.storage import StorageService
from app.projects.access import PROJECT_SHARE_SHARE_LEVELS, PROJECT_UPDATE_SHARE_LEVELS
from app.projects.schemas import ProjectFolderUpdate, ProjectShareCreate
from app.search.routes import _text_matches


def test_external_share_secret_hashing_is_deterministic_and_non_plaintext():
    value = "share-token-or-passcode"

    assert _hash_secret(value) == _hash_secret(value)
    assert _hash_secret(value) != value


def test_external_share_response_excerpt_limit_is_bounded():
    assert EXTERNAL_TEXT_EXCERPT_CHARS == 12_000


def test_storage_service_rejects_path_traversal(tmp_path):
    service = StorageService(root=tmp_path)

    with pytest.raises(ValueError):
        service.path_for_read("../outside.txt")


def test_text_snapshot_quality_threshold_controls_validation_status():
    assert _text_snapshot_validation_status("Readable enough", 0.54) == "needs_review"
    assert _text_snapshot_validation_status("", 0.99) == "needs_review"
    assert _text_snapshot_validation_status("Readable enough", 0.55) == "complete"


def test_contract_share_schema_defaults_to_view_only_without_download():
    payload = ContractShareCreate()

    assert payload.access_mode == ShareAccessMode.VIEW_ONLY
    assert payload.download_allowed is False


def test_contract_share_passcode_requires_minimum_length():
    with pytest.raises(ValidationError):
        ContractShareCreate(passcode="123")


def test_project_folder_update_can_clear_parent_folder():
    explicit_root = ProjectFolderUpdate(parent_folder_id=None)
    untouched = ProjectFolderUpdate()

    assert "parent_folder_id" in explicit_root.model_dump(exclude_unset=True)
    assert "parent_folder_id" not in untouched.model_dump(exclude_unset=True)


def test_project_share_access_levels_are_ordered():
    assert PROJECT_UPDATE_SHARE_LEVELS == {"update", "share"}
    assert PROJECT_SHARE_SHARE_LEVELS == {"share"}


def test_project_share_schema_restricts_access_levels():
    assert ProjectShareCreate(user_id="user-1", access_level="read").access_level == "read"
    assert ProjectShareCreate(user_id="user-1", access_level="update").access_level == "update"
    assert ProjectShareCreate(user_id="user-1", access_level="share").access_level == "share"

    with pytest.raises(ValidationError):
        ProjectShareCreate(user_id="user-1", access_level="admin")


def test_text_matches_returns_limited_case_insensitive_excerpts():
    text = "Alpha beta. " * 10

    matches = _text_matches(text, "BETA")

    assert len(matches) == 5
    assert matches[0]["start_char"] == 6
    assert "beta" in matches[0]["excerpt"]
