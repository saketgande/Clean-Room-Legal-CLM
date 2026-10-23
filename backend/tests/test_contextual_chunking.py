"""Contextual chunking: the contract's identity is prepended to the EMBEDDED
text only, never to the chunk we store and show the user.

Legal corpora are full of near-identical boilerplate, so an untagged chunk is
indistinguishable from the same clause in fifty other contracts. Tagging fixes
retrieval; leaking the tag into chunk_text would corrupt every quote and
citation built from it. Both halves have to hold at once.

No DB: a stub session keeps this runnable without Postgres.
"""

from types import SimpleNamespace

from app.ai import embeddings


class _StubSession:
    """Just enough Session for generate_embeddings_for_snapshot."""

    def __init__(self, title):
        self._title = title
        self.added = []

    def execute(self, _stmt):
        return None  # the delete-before-rebuild

    def scalar(self, _stmt):
        return self._title  # Contract.title lookup

    def add(self, row):
        self.added.append(row)


def _run(monkeypatch, *, contextual, title="Acme MSA"):
    seen = {}

    def fake_embed(texts):
        seen["inputs"] = list(texts)
        return [[0.0]] * len(texts)

    monkeypatch.setattr(embeddings, "_embed", fake_embed)
    monkeypatch.setattr(embeddings.settings, "contextual_chunking", contextual)

    snapshot = SimpleNamespace(
        id="snap-1",
        org_id="org-1",
        contract_id="c-1",
        contract_version_id="v-1",
        structure_status="flat_only",  # force the flat chunk_text path
        text="The Supplier shall indemnify the Customer. " * 40,
    )
    db = _StubSession(title)
    rows = embeddings.generate_embeddings_for_snapshot(
        db, snapshot=snapshot, created_by_user_id=None
    )
    return seen["inputs"], rows


def test_prefix_reaches_the_embedder_but_not_the_stored_chunk(monkeypatch):
    inputs, rows = _run(monkeypatch, contextual=True)
    assert inputs, "expected at least one chunk"
    assert all(t.startswith("[Contract: Acme MSA]\n") for t in inputs)
    # The stored text is what gets quoted back to the user — it must stay clean.
    assert all("[Contract:" not in r.chunk_text for r in rows)


def test_off_leaves_the_embedded_text_untouched(monkeypatch):
    inputs, rows = _run(monkeypatch, contextual=False)
    assert all("[Contract:" not in t for t in inputs)
    assert [r.chunk_text for r in rows] == inputs


def test_untitled_contract_is_not_prefixed(monkeypatch):
    # A missing title must not produce a "[Contract: None]" marker.
    inputs, _ = _run(monkeypatch, contextual=True, title=None)
    assert all("[Contract:" not in t for t in inputs)
