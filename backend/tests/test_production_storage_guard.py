"""INFRA-01 / DEP-01: production refuses to keep contract files on one machine's disk
unless a single-host deployment opts in, and a mismatched dependency explains how to
fix it instead of failing with a cryptic import error."""

import ast
import pathlib
import sys
import types

import pytest

from app.core import config
from app.core.config import settings, validate_runtime_settings


def _production(**overrides):
    safe = dict(
        environment="production", secret_key="s" * 40, setup_token="t" * 30,
        mock_claude=False, mock_docusign=False, mock_reducto=False, mock_resend=False, mock_sendgrid=False,
        mock_signa=False,
        mock_tmsearch=False, mock_serper=False, expose_password_reset_token_in_response=False,
        expose_refresh_token_in_body=False, refresh_cookie_secure=True, refresh_cookie_samesite="lax",
        allowed_hosts="clm.example.com", cors_origins="https://clm.example.com",
        docusign_connect_hmac_key="k" * 32, app_base_url="https://clm.example.com",
        storage_backend="s3", s3_bucket="clm-contracts", allow_local_storage_in_production=False,
        disable_rbac=False,
    )
    return settings.model_copy(update={**safe, **overrides})


def test_a_safe_production_config_boots():
    validate_runtime_settings(_production())


def test_production_refuses_to_boot_with_rbac_disabled():
    """DISABLE_RBAC makes every authenticated user an org admin (is_org_admin()
    consults has_permission()), which also voids confidentiality/MAC clearance
    checks. Booting a real deployment with it on is the failure this guards."""
    with pytest.raises(RuntimeError, match="DISABLE_RBAC"):
        validate_runtime_settings(_production(disable_rbac=True))


def test_production_refuses_local_disk_storage():
    with pytest.raises(RuntimeError, match="STORAGE_BACKEND=s3"):
        validate_runtime_settings(_production(storage_backend="local"))


def test_a_single_host_deployment_can_opt_in_to_local_storage():
    validate_runtime_settings(_production(storage_backend="local", allow_local_storage_in_production=True))


def test_s3_storage_needs_a_bucket():
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        validate_runtime_settings(_production(s3_bucket=None))


def test_development_is_not_affected():
    validate_runtime_settings(_production(environment="development", storage_backend="local"))


def test_an_outdated_pydantic_settings_explains_the_fix(monkeypatch):
    guard = next(node for node in ast.parse(pathlib.Path(config.__file__).read_text()).body if isinstance(node, ast.Try))
    older = types.ModuleType("pydantic_settings")
    older.BaseSettings, older.SettingsConfigDict = object, dict  # a release without NoDecode
    monkeypatch.setitem(sys.modules, "pydantic_settings", older)
    with pytest.raises(ImportError, match="requirements.lock"):
        # exec is the point: we re-run config.py's own import guard under a stubbed module.
        exec(compile(ast.Module(body=[guard], type_ignores=[]), "config.py", "exec"), {})  # noqa: S102
