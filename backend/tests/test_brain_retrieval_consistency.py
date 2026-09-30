"""RAG-03 / RAG-07 / PERF-03 / RAG-06: the assistant and the Brain page ground
answers in one retrieval path; graph facts count contracts, not edges; clause-reuse
matching is bounded; and shared entities are created under a lock."""

import inspect
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.ai.tool_runtime import ToolRuntime
from app.contract_brain import ingestion, retrieval


def test_the_assistant_uses_the_brain_pages_retrieval(monkeypatch):
    sources = {"semantic": [{"contract_title": "Acme MSA", "text": "Liability is capped."}], "clauses": [],
               "text": [], "graph": [{"fact": "Acme MSA --negotiated_with--> Acme Ltd"}]}
    calls = []
    monkeypatch.setattr(retrieval, "resolve_scope_contract_ids", lambda db, **kw: ["c-1"])
    monkeypatch.setattr(retrieval, "hybrid_sources", lambda db, **kw: calls.append(kw) or sources)
    user = SimpleNamespace(id="u-1", org_id="org-1")
    context = retrieval.assemble_context(None, user=user, question="Is liability capped?", scope="contract",
                                         contract_id="c-1")
    assert calls[0]["contract_ids"] == ["c-1"] and calls[0]["user"] is user
    assert context["context_text"] == retrieval.sources_to_context(sources)
    assert context["source_count"] == 2
    assert "assemble_context" in inspect.getsource(ToolRuntime._ask_contract_brain)


def test_a_question_nothing_matches_gets_no_filler_context(monkeypatch):
    empty = {"semantic": [], "clauses": [], "text": [], "graph": []}
    monkeypatch.setattr(retrieval, "resolve_scope_contract_ids", lambda db, **kw: ["c-1"])
    monkeypatch.setattr(retrieval, "hybrid_sources", lambda db, **kw: empty)
    context = retrieval.assemble_context(None, user=SimpleNamespace(org_id="org-1"), question="Where is the moon clause?",
                                         scope="portfolio", contract_id=None)
    assert (context["context_text"], context["source_count"]) == ("", 0)
    assert not hasattr(retrieval, "_fulltext_clauses")


class FakeDB:
    def __init__(self, executes, nodes):
        self.executes, self.nodes, self.statements = list(executes), nodes, []

    def execute(self, stmt):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        rows = self.executes.pop(0)
        return SimpleNamespace(all=lambda: rows)

    def scalars(self, _stmt):
        return iter(self.nodes)


def test_a_shared_entity_counts_distinct_contracts_and_lists_each_title_once():
    rule = SimpleNamespace(id="n-rule", label="Confidentiality rule", node_type="playbook_rule")
    outbound = [("n-rule", "c-2", {}), ("n-rule", "c-2", {}), ("n-rule", "c-2", {}), ("n-rule", "c-3", {})]
    db = FakeDB([[("n-rule", "c-1", "deviates_from")], outbound, [("c-2", "Acme MSA"), ("c-3", "Acme MSA")]], [rule])
    [fact] = retrieval.shared_entity_links(db, org_id="org-1", contract_ids=["c-1"], accessible_ids={"c-1", "c-2", "c-3"})
    assert fact["shared_with_count"] == 2 and fact["contract_ids"] == ["c-2", "c-3"]
    assert fact["fact"].endswith("also affects 2 other contract(s): Acme MSA")


def test_clause_reuse_matching_is_bounded_and_still_finds_the_template():
    text = "The Supplier shall indemnify and hold harmless the Customer against all third-party claims."
    mine = [("indemnification", text, "Indemnity")]
    candidates = [("indemnification", "Governing law is England and Wales.", "Gamma MSA"),
                  ("indemnification", text.replace(" all ", " any "), "Beta MSA")]
    db = FakeDB([mine, candidates], [])
    [fact] = retrieval.clause_language_matches(db, org_id="org-1", contract_ids=["c-1"], accessible_ids={"c-1", "c-2"})
    assert "Beta MSA" in fact["fact"] and fact["similarity"] >= 80
    assert len(db.statements) == 2 and all("LIMIT" in sql for sql in db.statements)


def test_shared_entities_are_created_under_a_lock_and_looked_up_again():
    source = inspect.getsource(ingestion.ingest_contract_brain)
    lock_at = source.index("pg_advisory_xact_lock")
    assert source.index('KnowledgeNode.properties["entity_key"]', lock_at) > lock_at
    assert source.index("node = KnowledgeNode(", lock_at) > lock_at
