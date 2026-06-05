"""F-16 boot-time integration-key validation tests."""

from __future__ import annotations


def _make_settings(**overrides):
    from proposals.audit_2026_06_02.rewrites.F16_boot_integration_key_check import (  # type: ignore[import-not-found]
        core_config_validate as mod,
    )

    defaults = {
        "environment": "production",
        "mock_claude": False,
        "claude_api_key": "real-key",
        "mock_reducto": False,
        "reducto_api_key": "real-key",
        "mock_resend": False,
        "resend_api_key": "real-key",
        "mock_docusign": True,
        "docusign_integration_key": None,
        "docusign_user_id": None,
        "docusign_account_id": None,
        "docusign_private_key_path": None,
    }
    defaults.update(overrides)
    return mod.Settings(**defaults)


def test_boot_rejects_missing_claude_key_when_real():
    """``MOCK_CLAUDE=false`` + missing key raises with the env-var name."""
    from proposals.audit_2026_06_02.rewrites.F16_boot_integration_key_check import (  # type: ignore[import-not-found]
        core_config_validate as mod,
    )

    settings = _make_settings(mock_claude=False, claude_api_key=None)
    raised = False
    try:
        mod.validate_runtime_settings(settings)
    except RuntimeError as exc:
        raised = "CLAUDE_API_KEY" in str(exc)
    assert raised


def test_boot_rejects_missing_docusign_pem_when_real(tmp_path):
    """``MOCK_DOCUSIGN=false`` + missing PEM file path raises with a clear error."""
    from proposals.audit_2026_06_02.rewrites.F16_boot_integration_key_check import (  # type: ignore[import-not-found]
        core_config_validate as mod,
    )

    pem_path = tmp_path / "missing.pem"
    settings = _make_settings(
        mock_docusign=False,
        docusign_integration_key="ik",
        docusign_user_id="u",
        docusign_account_id="a",
        docusign_private_key_path=str(pem_path),
    )
    raised = False
    try:
        mod.validate_runtime_settings(settings)
    except RuntimeError as exc:
        raised = "missing file" in str(exc).lower()
    assert raised


def test_local_warnings_dont_block_boot(caplog):
    """In a local env, mock-on + real-key-set logs a warning but does NOT raise."""
    from proposals.audit_2026_06_02.rewrites.F16_boot_integration_key_check import (  # type: ignore[import-not-found]
        core_config_validate as mod,
    )

    settings = _make_settings(
        environment="local",
        mock_claude=True,
        claude_api_key="key-still-set",
        mock_reducto=True,
        mock_resend=True,
        mock_docusign=True,
    )
    # Should NOT raise.
    mod.validate_runtime_settings(settings)
