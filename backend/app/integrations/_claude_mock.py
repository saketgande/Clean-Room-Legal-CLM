"""Mock Claude responses, isolated from the production HTTP client.

Pulled out of `claude.py` so the production code surface is uncluttered and
the keyword-matching test fixtures can be unit-tested independently.
"""

from __future__ import annotations

from typing import Any


def structured_payload_by_tool() -> dict[str, dict[str, Any]]:
    """Canned tool_use payloads returned when ``settings.mock_claude`` is on.

    Add new tools here when new structured skills are introduced.
    """
    return {
        "return_contract_metadata_extraction": {
            "title": None,
            "contract_type": None,
            "counterparty_name": None,
            "jurisdiction": None,
            "risk_level": None,
            "value_amount": None,
            "currency": None,
            "effective_date": None,
            "expiration_date": None,
            "confidence": "low",
            "citations": [],
            "notes": "Mock Claude mode returned no extracted metadata.",
        },
        "return_clause_extraction": {
            "clauses": [],
            "extraction_notes": "Mock Claude mode returned no clauses.",
        },
        "return_obligation_extraction": {
            "obligations": [],
            "extraction_notes": "Mock Claude mode returned no obligations.",
        },
        "return_renewal_extraction": {"confidence": "low", "needs_review": True},
        "return_tabular_cell_extraction": {
            "answer": "",
            "not_found": True,
            "confidence": "low",
            "citations": [],
        },
        "return_tabular_review_chat": {
            "answer": "Mock Claude mode is enabled, so no live tabular-review answer was generated.",
            "citations": [],
        },
        "return_contract_brain_query_parse": {
            "query_scope": "portfolio",
            "target_clause_types": [],
            "needs_vector_search": True,
            "needs_graph_search": True,
            "needs_full_text_search": True,
        },
        "return_contract_brain_answer": {
            "answer": "Mock Claude mode is enabled, so no live Contract Brain answer was generated.",
            "citations": [],
            "confidence": "low",
            "limitations": "Mock mode: no model reasoning performed.",
        },
        "return_assistant_streaming": {
            "answer": "Mock Claude mode is enabled, so no live legal answer was generated.",
            "citations": [],
            "tool_results": [],
        },
        "return_contract_docx_generation": {
            "title": "Generated Contract",
            "sections": [{"heading": "Overview", "body": "Draft content placeholder generated in mock Claude mode."}],
            "assumptions": ["Mock Claude mode is enabled."],
            "citations": [],
        },
        "return_contract_edit_suggestions": {"edits": [], "summary": "Mock Claude mode returned no edits."},
        "return_playbook_review": {
            "deviations": [],
            "summary": "Mock Claude mode returned no playbook deviations.",
            "citations": [],
        },
    }


def select_mock_tool(
    *,
    user_text: str,
    tool_names: set[str],
) -> tuple[str | None, dict[str, Any]]:
    """Pick which of the available tools to "call" for a user message.

    The selection is intentionally simple keyword matching; the goal is to
    exercise the streaming/tool-use code paths in mock mode, not to perform
    real reasoning. Returning (None, {}) means "respond with plain text".
    """
    text = user_text.lower()
    if "find" in text and "find_in_contract" in tool_names:
        return "find_in_contract", {
            "contract_handle": "contract-0",
            "query": _mock_query(text),
        }
    if "playbook" in text and "redline" in text and "redline_against_playbook" in tool_names:
        return "redline_against_playbook", {
            "contract_handle": "contract-0",
            "playbook_id": "mock-playbook-id",
        }
    if "playbook" in text and "run_playbook_review" in tool_names:
        return "run_playbook_review", {
            "contract_handle": "contract-0",
            "playbook_id": "mock-playbook-id",
        }
    if ("edit" in text or "redline" in text) and "edit_contract" in tool_names:
        return "edit_contract", {
            "contract_handle": "contract-0",
            "instructions": "Apply the requested contract edit.",
        }
    if ("generate" in text or "draft" in text) and "generate_contract_docx" in tool_names:
        return "generate_contract_docx", {
            "title": "Generated Contract",
            "instructions": "Generate a contract draft from the user request.",
        }
    if "replicate" in text and "replicate_contract_version" in tool_names:
        return "replicate_contract_version", {"contract_handle": "contract-0"}
    if "status" in text and "get_contract_status" in tool_names:
        return "get_contract_status", {"contract_handle": "contract-0"}
    if ("workflow" in text or "workflows" in text) and "list_workflows" in tool_names:
        return "list_workflows", {}
    if ("project" in text or "contracts" in text) and "list_project_contracts" in tool_names:
        return "list_project_contracts", {"project_id": "mock-project-id"}
    if (
        ("contract" in text or "summar" in text or "read" in text)
        and "read_contract" in tool_names
    ):
        return "read_contract", {"contract_handle": "contract-0"}
    return None, {}


def _mock_query(user_text: str) -> str:
    quoted = user_text.split('"')
    if len(quoted) >= 3 and quoted[1].strip():
        return quoted[1].strip()
    words = [word.strip(".,?!:;") for word in user_text.split()]
    ignored = {"find", "in", "the", "contract", "for", "show", "me", "search"}
    for word in reversed(words):
        if word and word not in ignored:
            return word
    return "agreement"
