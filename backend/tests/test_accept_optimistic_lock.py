import inspect

from app.contract_files.routes import accept_contract_edit


def test_accept_guards_against_stale_redline_reverting_newer_changes():
    """A redline is a whole-document proposal built from a specific base version.
    Accepting swaps the authoritative pointer to that proposal, discarding
    anything the base didn't contain. So accept MUST refuse when the base is no
    longer current (another redline was accepted, or a new version uploaded) —
    otherwise it silently reverts that newer state. This is an optimistic-lock
    check, and it must run before the pointer swap."""
    src = inspect.getsource(accept_contract_edit)
    guard = "edit.contract_version_id != contract.current_authoritative_version_id"
    swap = "contract.current_authoritative_version_id = proposal_version.id"

    assert guard in src, "missing optimistic-lock guard on accept"
    assert "HTTP_409_CONFLICT" in src, "stale accept must return 409 Conflict"
    assert src.index(guard) < src.index(swap), "guard must run before the pointer swap"
