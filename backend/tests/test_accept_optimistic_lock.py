import inspect

from app.contract_files.routes import _apply_decided_redline, accept_contract_edit


def test_accept_guards_against_stale_redline_reverting_newer_changes():
    """Each redline edit is anchored to character positions in a specific base
    version. Accepting MUST refuse when that base is no longer current (another
    redline was applied, or a new version uploaded) — otherwise applying the edit
    would silently revert that newer state. The same check runs again right
    before the accepted edits become the new authoritative version."""
    guard = "edit.contract_version_id != contract.current_authoritative_version_id"
    accept_src = inspect.getsource(accept_contract_edit)
    assert guard in accept_src, "missing optimistic-lock guard on accept"
    assert "HTTP_409_CONFLICT" in accept_src, "stale accept must return 409 Conflict"
    assert accept_src.index(guard) < accept_src.index('edit.status = "accepted"')

    apply_src = inspect.getsource(_apply_decided_redline)
    stale = "base_version_id != contract.current_authoritative_version_id"
    swap = "promote_version(db, contract=contract, version=new_version"
    assert stale in apply_src and swap in apply_src
    assert apply_src.index(stale) < apply_src.index(swap), "guard must run before the pointer swap"
