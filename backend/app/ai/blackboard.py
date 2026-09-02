"""Session blackboard — shared working memory for the agent network.

Every substantive agent/tool result in a session is recorded here as a compact
"finding", and the whole board is injected into each turn's context. That gives
two things the old design lacked:

  1. Memory across turns — a follow-up sees what was already established instead
     of re-reading the contract from scratch.
  2. Connection between agents — the research agent's answer, the drafting
     agent's draft and the review agent's redline all land on the SAME board,
     so a later agent builds on earlier ones instead of starting cold.

Stored on ``AssistantSession.metadata_json["blackboard"]`` (a JSON column that
already defaults to ``{}`` — same pattern as AssistantContractHandle), so no new
table. Bounded to the most recent findings so the board can't grow unbounded.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.assistant.models import AssistantSession

_MAX_FINDINGS = 30


def record_finding(
    db: Session,
    *,
    session_id: str,
    agent: str,
    summary: str,
    contract_id: str | None = None,
    ref: str | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> None:
    """Append a finding to the session board (newest kept, bounded)."""
    session = db.get(AssistantSession, session_id)
    if session is None or not (summary or "").strip():
        return
    meta = dict(session.metadata_json or {})
    board = dict(meta.get("blackboard") or {})
    findings = list(board.get("findings") or [])
    findings.append(
        {
            "agent": agent,
            "summary": summary.strip()[:600],
            "contract_id": contract_id,
            "ref": ref,
            "citations": [
                {"quote": (c or {}).get("quote", "")[:240], "label": (c or {}).get("label")}
                for c in (citations or [])[:2]
            ],
        }
    )
    board["findings"] = findings[-_MAX_FINDINGS:]
    meta["blackboard"] = board
    session.metadata_json = meta  # reassign so SQLAlchemy detects the JSON change
    db.add(session)


def render_blackboard(db: Session, *, session_id: str) -> str:
    """A compact text block of prior findings for injection into the turn.
    Empty string when the board is empty (nothing to inject on a fresh chat)."""
    session = db.get(AssistantSession, session_id)
    if session is None:
        return ""
    findings = (dict(session.metadata_json or {}).get("blackboard") or {}).get("findings") or []
    if not findings:
        return ""
    lines = []
    for f in findings:
        tag = f.get("ref") or f.get("contract_id") or ""
        who = f.get("agent") or "agent"
        head = f"- [{who}{(' · ' + tag) if tag else ''}] {f.get('summary')}"
        lines.append(head)
    return (
        "Shared working memory — what the agents have already established this "
        "session. Build on it; do not re-fetch or re-derive what's already here:\n"
        + "\n".join(lines)
    )


# Which tool results are worth remembering, and how to summarise each into a
# finding. Reads/looks-ups are omitted — a finding is a conclusion or artifact,
# not "I opened a file". Returns (agent, summary, contract_id, ref, citations)
# or None to skip.
def finding_from_tool(tool_name: str, result: dict[str, Any]) -> tuple | None:
    if not isinstance(result, dict) or result.get("error"):
        return None
    cid = result.get("contract_id")
    if tool_name == "ask_contract_brain":
        ans = result.get("answer")
        if not ans:
            return None
        return ("research", ans, cid, None, result.get("citations"))
    if tool_name in ("generate_contract_docx", "redraft_contract"):
        return ("drafting", f"Drafted a contract ({result.get('title') or 'document'}).", cid, None, None)
    if tool_name in ("edit_contract", "redline_against_playbook", "run_playbook_review"):
        n = len(result.get("deviations") or result.get("edits") or [])
        label = "redline" if "contract_version_id" in result else "review"
        return ("review", f"Playbook {label} produced {n} item(s) for review.", cid, None, None)
    if tool_name == "create_intake_request":
        return ("intake", f"Raised intake request {result.get('ref')}.", None, result.get("ref"), None)
    if tool_name == "extract_obligations":
        n = len(result.get("obligations") or [])
        return ("obligations", f"Extracted {n} obligation(s).", cid, None, None)
    return None
