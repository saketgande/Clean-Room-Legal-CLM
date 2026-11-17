"""The AI gateway is the only code that calls Claude.

The guardrails run inside the gateway (app/ai/gateway/gateway.py), so any code that called Claude some other way
would skip them. This test fails if a file outside the gateway and the Claude client calls the provider directly.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# The only files allowed to talk to Claude: the gateway, and the client it wraps.
ALLOWED = ("ai/gateway/", "integrations/claude.py")

DIRECT_CALLS = re.compile(
    r"\.(complete_structured|complete_vision_structured|complete_text|complete_json|complete_with_tools|stream_with_tools)\("
    r"|\.messages\.(create|stream)\("
    r"|\b(Async)?Anthropic\("
)


def test_nothing_outside_the_gateway_calls_claude():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative.startswith(ALLOWED[0]) or relative == ALLOWED[1]:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if DIRECT_CALLS.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert not offenders, "Claude is called outside the AI gateway (the guardrails would be skipped):\n" + "\n".join(
        offenders
    )


def test_the_check_would_notice_a_direct_call():
    assert DIRECT_CALLS.search("response = await client.complete_text(system_prompt=p, user_prompt=q)")
    assert DIRECT_CALLS.search("client.messages.create(model=m)")
    assert not DIRECT_CALLS.search("result = await gateway_for(claude_client).text(db, 'x', ctx=ctx, user_prompt=q)")
