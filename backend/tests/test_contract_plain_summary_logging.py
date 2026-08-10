"""contract_plain_summary degrades to a deterministic fallback on any AI
failure by design (never block a lawyer from seeing risk info over an API
hiccup) — but it previously did so with a bare `except Exception: return
fallback`, so an expired key or an outage was indistinguishable from normal
operation in the logs. Pin that the failure is now actually logged."""

import inspect

from app.contracts.routes import contract_plain_summary


def test_plain_summary_failure_is_logged_not_silent():
    source = inspect.getsource(contract_plain_summary)

    assert "except Exception:" in source
    fallback_branch = source.split("except Exception:", 1)[1]

    assert "logger.warning(" in fallback_branch
    assert "exc_info=True" in fallback_branch
    # Still degrades gracefully — logging the failure must not turn into raising.
    assert '"summary": _fallback()' in fallback_branch
