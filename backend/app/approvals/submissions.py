"""Which approval rows make up a contract's *current* approval chain.

Every submission for approval creates one row per rung, and all of them are stamped
with the same ``submission_batch_id`` (see ``ApprovalsService.submit_subject_for_approval``).
Rows are never deleted: a rejected or superseded submission stays behind as history.
Anything that asks "is this version approved?" must therefore look at the LATEST
submission only, or an old REJECTED row would veto an approval that has since
succeeded.

Kept free of database access so the rule can be tested on plain objects.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import TypeVar

# Chains created before ``submission_batch_id`` existed carry no id. Their rungs were
# inserted together, so they are grouped by creation time — the same window the
# chain-progress view (``approval_chain`` in approvals/routes.py) uses for them.
LEGACY_CHAIN_WINDOW = timedelta(seconds=10)

Row = TypeVar("Row")


def _batch_id(row) -> str | None:
    return (row.metadata_json or {}).get("submission_batch_id")


def latest_submission(rows: Sequence[Row]) -> list[Row]:
    """The rows created by the most recent submission.

    ``rows`` are the approval rows of ONE subject and version (callers decide whether
    cancelled rows are included). The newest row identifies the latest submission:
    its batch id picks every rung of that submission; without a batch id (older data)
    the rows created within ``LEGACY_CHAIN_WINDOW`` of it, themselves without one,
    are taken as the chain.
    """
    if not rows:
        return []
    newest = max(rows, key=lambda row: row.created_at)
    batch = _batch_id(newest)
    if batch:
        return [row for row in rows if _batch_id(row) == batch]
    cutoff = newest.created_at - LEGACY_CHAIN_WINDOW
    return [row for row in rows if _batch_id(row) is None and row.created_at >= cutoff]
