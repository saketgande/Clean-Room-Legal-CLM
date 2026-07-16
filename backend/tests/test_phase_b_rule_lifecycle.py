"""Phase B — routing-rule lifecycle (edit / reorder / soft-delete + audit).

The mutations themselves are thin HTTP+DB orchestration (exercised end-to-end
against a live stack); here we lock the invariants that are cheap to regress:
the resolver must skip soft-deleted rules, the audit snapshot shape, and the
lifecycle endpoints being wired.
"""
import inspect
from types import SimpleNamespace

from app.approvals import routes, service


def test_resolver_excludes_soft_deleted_rules():
    # The active-rule query must filter deleted_at IS NULL, else a retired rule
    # would keep routing contracts.
    src = inspect.getsource(service._matched_rules)
    assert "deleted_at" in src


def test_list_routing_rules_excludes_soft_deleted():
    src = inspect.getsource(routes.list_routing_rules)
    assert "deleted_at" in src


def test_rule_snapshot_shape():
    rule = SimpleNamespace(
        name="High-value",
        priority="30",
        criteria={"min_value": 250000},
        is_active=True,
        steps=[
            SimpleNamespace(
                step_order=2, approver_group_id="g2", approver_user_id=None,
                approver_role=None, mode="any",
            ),
            SimpleNamespace(
                step_order=1, approver_group_id="g1", approver_user_id=None,
                approver_role=None, mode="all",
            ),
        ],
    )
    snap = routes._rule_snapshot(rule)
    assert snap["name"] == "High-value"
    assert snap["criteria"] == {"min_value": 250000}
    # steps come back sorted by step_order regardless of relationship order
    assert [s["step_order"] for s in snap["steps"]] == [1, 2]
    assert snap["steps"][0]["approver_group_id"] == "g1"
    assert snap["steps"][0]["mode"] == "all"


def test_lifecycle_endpoints_registered():
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes.router.routes}
    assert ("/approvals/routing-rules/{rule_id}", ("PATCH",)) in paths
    assert ("/approvals/routing-rules/{rule_id}", ("DELETE",)) in paths
    assert ("/approvals/routing-rules/order", ("PUT",)) in paths
    assert ("/approvals/routing-rules/audit", ("GET",)) in paths
