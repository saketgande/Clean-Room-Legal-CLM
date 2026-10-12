"""Guard rail: every Claude call goes through the AI gateway.

The gateway (app/ai/gateway) is what adds the prompt override, the
untrusted-input guard, the token cap, the output check and the ledger row. A
direct call to the Claude client skips all five, so any one outside the client
itself and the gateway fails here. Use app.ai.gateway.gateway_for(...) instead.

This started as a ratchet listing the call sites still to move; the last ones
(docstudio's three dev-page calls) went with docstudio in Phase 5, so the
allowed list is now empty.
"""

import ast
from collections import Counter
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
CLIENT_METHODS = {
    "complete_structured",
    "complete_vision_structured",
    "complete_text",
    "complete_with_tools",
    "stream_with_tools",
}
# Where the client itself and the gateway live.
EXEMPT_DIRS = ("integrations", "ai/gateway")


def _direct_calls() -> Counter:
    found: Counter = Counter()
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith(EXEMPT_DIRS):
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=rel)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CLIENT_METHODS:
                found[rel] += 1
    return found


def test_no_direct_claude_calls_outside_the_gateway():
    assert dict(_direct_calls()) == {}, "call Claude through app.ai.gateway, not the client"
