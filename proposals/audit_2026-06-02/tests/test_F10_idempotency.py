"""F-10 idempotency-key tests."""

from __future__ import annotations

from datetime import UTC, datetime


def test_manual_extract_within_5min_deduped():
    """Two extractions inside the same 5-minute bucket dedupe to one key."""
    from app.jobs.idempotency import build_debounce_token, build_idempotency_key

    user_id = "user-1"
    t1 = datetime(2026, 6, 2, 12, 1, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 2, 12, 4, 59, tzinfo=UTC)
    key_one = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
        debounce_token=build_debounce_token(user_id=user_id, now=t1),
    )
    key_two = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
        debounce_token=build_debounce_token(user_id=user_id, now=t2),
    )
    assert key_one == key_two


def test_manual_extract_after_5min_allowed():
    """A click in the next bucket yields a different key."""
    from app.jobs.idempotency import build_debounce_token, build_idempotency_key

    user_id = "user-1"
    t1 = datetime(2026, 6, 2, 12, 1, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 2, 12, 6, 0, tzinfo=UTC)
    key_one = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
        debounce_token=build_debounce_token(user_id=user_id, now=t1),
    )
    key_two = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
        debounce_token=build_debounce_token(user_id=user_id, now=t2),
    )
    assert key_one != key_two


def test_assistant_trigger_distinct_from_manual():
    """The ``trigger`` field discriminates assistant vs manual reruns."""
    from app.jobs.idempotency import build_idempotency_key

    k_manual = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
    )
    k_assistant = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="assistant",
    )
    assert k_manual != k_assistant
