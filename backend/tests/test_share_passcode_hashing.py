"""SEC-01: share passcodes (chosen by people) are stored with bcrypt, old SHA-256
hashes are upgraded on their next successful use, and the passcode travels in a
header instead of the URL."""

import inspect
from types import SimpleNamespace

from app.ai import tool_runtime
from app.contract_files import routes
from app.core.config import settings


def test_new_share_passcodes_are_hashed_with_bcrypt():
    for module in (routes, tool_runtime):
        source = inspect.getsource(module)
        assert "passcode_hash=hash_password(payload.passcode)" in source
        assert "passcode_hash=_hash_secret(" not in source


def test_a_legacy_sha256_passcode_works_and_is_upgraded():
    share = SimpleNamespace(passcode_hash=routes._hash_secret("harbour-view-22"))
    db = SimpleNamespace(commit=lambda: None)
    assert routes._passcode_matches(db, share, "harbour-view-22") is True
    assert share.passcode_hash.startswith("$2")
    assert routes._passcode_matches(db, share, "harbour-view-22") is True
    assert routes._passcode_matches(db, share, "wrong-passcode") is False


def test_the_passcode_is_sent_in_a_header_not_the_url():
    for fn in (routes.view_external_share, routes.list_external_share_comments,
               routes.add_external_share_comment, routes.download_external_share):
        assert inspect.signature(fn).parameters["passcode"].default.alias == "X-Share-Passcode", fn.__name__
    assert "X-Share-Passcode" in settings.cors_allow_headers.split(",")
