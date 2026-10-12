"""Step 3e, chat side: the `query_contracts` tool and the fixes around it.

Ask Aegis answered "which contracts ..." from the top few search snippets, called 8 of 20
"high confidence", and its list tools forgot deleted contracts and never said how many more
there were. These tests run the chat tools against real Postgres rows inside one rolled-back
transaction, in an organisation of their own (so the dev database's rows never change a
count). The model is never called; the AI reading of contracts goes to an in-memory broker
nobody consumes, and the "worker" is played by the test settling the table's cells.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai import tool_runtime as tr
from app.ai.controller import ai_controller
from app.ai.schemas import BrainAnswerOutput, CitationInput
from app.ai.tool_registry import (
    AttentionItemsInput,
    BrainAskInput,
    FindContractsInput,
    ListNoticesInput,
    ListObligationsInput,
    ListRenewalsInput,
    QueryContractsInput,
    tool_registry,
)
from app.assistant.models import (
    AssistantContractHandle,
    AssistantSession,
    AssistantToolCall,
)
from app.assistant.runner import accumulate_block
from app.auth.models import User
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine, utcnow
from app.core.enums import AssistantToolCategory
from app.obligations.models import Obligation
from app.organizations.models import Organization
from app.renewals.models import RenewalEvent
from app.tabular_review.models import TabularReview, TabularReviewCell

WORDING = "Does this contract renew automatically?"


@pytest.fixture
def conn():
    connection = engine.connect()
    trans = connection.begin()
    try:
        yield connection
    finally:
        trans.rollback()
        connection.close()


@pytest.fixture
def db(conn):
    # expire_on_commit=False like the app's own SessionLocal, so the tool's expire_all() is
    # what makes it see the worker's writes.
    session = Session(bind=conn, expire_on_commit=False)
    yield session
    session.close()


# Long enough (6+ words) to verify a yes; the filter ignores shorter quotes.
RENEWS = "This Agreement renews automatically for successive one-year terms."

class World:
    """One organisation, one owner and a chat session; contracts are the owner's."""

    def __init__(self, db: Session):
        self.db = db
        org = Organization(name="Chat Co", slug=f"chat-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.owner = User(org_id=org.id, email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
                          full_name="owner", hashed_password="hash", status="active")
        db.add(self.owner)
        db.flush()
        session = AssistantSession(org_id=org.id, created_by_user_id=self.owner.id)
        db.add(session)
        db.flush()
        self.session_id = session.id

    def contract(self, title: str = "Contract", **fields) -> Contract:
        contract = Contract(org_id=self.org_id, title=title, owner_user_id=self.owner.id, **fields)
        self.db.add(contract)
        self.db.flush()
        return contract

    # The text carries the sentence the fake reading quotes, so a "yes" verifies against it.
    def with_text(self, contract: Contract, text: str = f"{RENEWS} Each party keeps the other's secrets.") -> Contract:
        db = self.db
        file = ContractFile(org_id=self.org_id, contract_id=contract.id, file_label="c.docx")
        stored = StorageObject(org_id=self.org_id, storage_key=f"test/{uuid.uuid4().hex}", filename="c.docx",
                               mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
        db.add_all([file, stored])
        db.flush()
        version = ContractVersion(org_id=self.org_id, contract_id=contract.id, contract_file_id=file.id,
                                  version_number=1, storage_object_id=stored.id, is_authoritative=True)
        db.add(version)
        db.flush()
        snap = ContractTextSnapshot(org_id=self.org_id, contract_id=contract.id,
                                    contract_version_id=version.id, extraction_method="text", text=text)
        db.add(snap)
        db.flush()
        version.text_snapshot_id = snap.id
        contract.current_authoritative_version_id = version.id
        db.flush()
        return contract

    def renewal(self, contract: Contract, **fields) -> RenewalEvent:
        event_row = RenewalEvent(org_id=self.org_id, contract_id=contract.id, **fields)
        self.db.add(event_row)
        self.db.flush()
        return event_row

    def obligation(self, contract: Contract, due, text: str = "Send the report") -> Obligation:
        row = Obligation(org_id=self.org_id, contract_id=contract.id, description=text, due_date=due,
                         status="open")
        self.db.add(row)
        self.db.flush()
        return row


@pytest.fixture
def world(db, monkeypatch):
    from app.contract_brain import portfolio

    # These users carry no roles; the AI tools permission a reading needs is pinned in
    # tests/test_portfolio_filter.py::test_reading_needs_the_ai_tools_permission.
    monkeypatch.setattr(portfolio, "has_permission", lambda perms, needed: True)
    return World(db)


async def query(world: World, **fields) -> dict:
    fields.setdefault("question", "Which contracts?")
    return await tr.tool_runtime._query_contracts(
        world.db, payload=QueryContractsInput(**fields), user=world.owner, session_id=world.session_id)


def handle_selects(conn):
    """Collects every SELECT on the handle table while the test runs."""
    seen: list[str] = []

    def spy(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "FROM assistant_contract_handle" in statement:
            seen.append(statement)

    event.listen(conn, "before_cursor_execute", spy)
    return seen, lambda: event.remove(conn, "before_cursor_execute", spy)


# --- the tool is registered, offered, gated and dispatched ------------------------------------------


def test_query_contracts_is_registered_runs_without_asking_and_is_dispatched():
    """A tool added to the registry but missing from the dispatch chain silently answers
    "feature_not_enabled"; one registered READ_ONLY would hide that it can spend tokens."""
    spec = tool_registry.get("query_contracts")

    assert spec.category == AssistantToolCategory.MUTATING
    assert spec.required_permission == "assistant:use"
    assert spec.requires_confirmation is False
    assert 'tool_name == "query_contracts"' in inspect.getsource(tr.ToolRuntime._execute_validated)


def test_the_model_is_offered_a_typed_filter_it_cannot_misuse(db):
    """The model-facing schema carries every PortfolioFilter condition plus the question,
    and only the question is required (a filter with nothing set means "every contract")."""
    user = SimpleNamespace(org_id="org-1", permission_values={"*"})

    [offered] = [t for t in ai_controller._assistant_tool_schemas(db, user=user)
                 if t["name"] == "query_contracts"]

    schema = offered["input_schema"]
    assert {"question", "contract_types", "counterparty", "governing_law", "stages", "risk_bands",
            "effective", "expires", "value_min", "value_max", "currency", "wording",
            "count_only"} <= set(schema["properties"])
    assert schema["required"] == ["question"]
    assert "wording" in offered["description"] and "EVERY" in offered["description"]
    json.dumps(schema)


async def test_the_tool_needs_the_assistant_permission():
    """A user without assistant:use must not reach a tool that can start a paid reading."""
    nobody = SimpleNamespace(id="u-1", org_id="org-1", permission_values=set())

    with pytest.raises(HTTPException) as exc:
        await tr.tool_runtime.execute(
            None, tool_name="query_contracts", tool_input={"question": "Which contracts?"},
            user=nobody, session_id="s-1")

    assert exc.value.status_code == 403 and "assistant:use" in exc.value.detail


async def test_a_field_question_is_answered_through_execute_over_every_contract(world, db, monkeypatch):
    """The whole path: validated input -> filter over EVERY accessible contract -> counts, the
    matches with handles, and a saved call. Nothing is asked to be confirmed."""
    monkeypatch.setattr(tr, "has_permission", lambda *a, **k: True)
    for i in range(3):
        world.contract(f"NDA {i}", contract_type="NDA")
    world.contract("MSA", contract_type="MSA")

    out = await tr.tool_runtime.execute(
        db, tool_name="query_contracts", user=world.owner, session_id=world.session_id,
        tool_input={"question": "Which NDAs do we have?", "contract_types": ["NDA"]})

    assert "confirmation_required" not in out
    assert out["summary"] == "Checked 4 of 4 contracts · 3 match"
    assert (out["total"], out["checked"], out["matched"], out["complete"]) == (4, 4, 3, True)
    assert out["conditions"] == ["type: NDA"] and out["confidence"] == "high"
    assert sorted(m["title"] for m in out["matches"]) == ["NDA 0", "NDA 1", "NDA 2"]
    assert "reading" not in out and "review_url" not in out
    [call] = db.scalars(select(AssistantToolCall).where(AssistantToolCall.org_id == world.org_id)).all()
    assert call.status == "succeeded" and call.result["matched"] == 3


# --- handles are minted in one pass -------------------------------------------------------------------


async def test_handles_are_minted_in_one_pass_and_the_list_carries_no_contract_id(world, db, conn):
    """Every contract_id in a result costs the model-facing copy two queries (and loads every
    handle row each time); a few hundred matches meant ~600 queries. The lists carry handles
    minted with ONE read of the chat's handles, and no contract_id for that copy to chase."""
    contracts = [world.contract(f"Vendor {i:02d}") for i in range(40)]
    db.add(AssistantContractHandle(org_id=world.org_id, session_id=world.session_id,
                                   contract_id=contracts[7].id, handle="contract-0"))
    db.flush()
    seen, stop = handle_selects(conn)
    try:
        out = await query(world)
        minting = len(seen)
        safe = ai_controller._model_safe_result(
            db, value=out, org_id=world.org_id, session_id=world.session_id, user_id=world.owner.id)
    finally:
        stop()

    assert minting == 1
    assert len(seen) == 1  # the model-facing copy asked for no handle at all
    assert safe == out
    assert "contract_id" not in json.dumps(out)
    handles = {m["title"]: m["contract_handle"] for m in out["matches"]}
    assert len(handles) == 40 and len(set(handles.values())) == 40
    assert handles["Vendor 07"] == "contract-0"  # an existing handle is kept
    assert sorted(handles.values(), key=lambda h: int(h.split("-")[1])) == [f"contract-{i}" for i in range(40)]


# --- wording: the AI reads, code decides, and the tool waits only so long -----------------------------


class Clock:
    """Stands in for asyncio.sleep + time.monotonic so a 90 second wait takes no time, and
    runs `on_sleep` as the "worker" between polls."""

    def __init__(self, monkeypatch, db, on_sleep=None):
        self.now, self.sleeps, self.open_transactions = 0.0, [], []
        self.db, self.on_sleep = db, on_sleep
        monkeypatch.setattr(tr, "asyncio", SimpleNamespace(sleep=self.sleep))
        monkeypatch.setattr(tr, "time", SimpleNamespace(monotonic=lambda: self.now))

    async def sleep(self, seconds):
        self.open_transactions.append(self.db.in_transaction())
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


def settle(db, contract_id: str, *, verdict: str, quote: str = RENEWS) -> None:
    cell = db.scalar(select(TabularReviewCell).where(TabularReviewCell.contract_id == contract_id))
    cell.status = "complete"
    cell.raw_ai_output = {"verdict": verdict}
    cell.citations = [{"quote": quote}]
    db.flush()


async def test_a_wording_question_waits_for_the_reading_then_decides_from_the_verdicts(world, db, monkeypatch):
    """The reading finishes while the tool waits: the answer is the finished list (code
    decided from the typed verdicts), carries the cost estimate and the table link, and the
    model gets no review id it could not use."""
    yes = world.with_text(world.contract("Renews"))
    no = world.with_text(world.contract("Fixed term"))

    def worker(_poll):
        settle(db, yes.id, verdict="yes")
        settle(db, no.id, verdict="no")

    clock = Clock(monkeypatch, db, on_sleep=worker)

    out = await query(world, wording=[WORDING], question="Which contracts renew automatically?")

    assert clock.sleeps == [tr.QUERY_POLL_SECONDS]
    assert out["summary"] == "Checked 2 of 2 contracts · 1 match"
    assert [m["title"] for m in out["matches"]] == ["Renews"]
    assert out["matches"][0]["why"] == [f"{WORDING} Yes — “{RENEWS}”"]
    assert out["unknown"] == [] and out["complete"] is True
    assert out["reading"]["contracts"] == 2 and out["reading"]["done"] == 2 and out["reading"]["estimate_usd"] > 0
    assert "reading_note" not in out
    [review] = db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all()
    assert out["review_url"] == f"/tabular-reviews/{review.id}"
    assert "tabular_review_id" not in json.dumps(out)


async def test_an_unfinished_reading_returns_progress_after_the_wait_and_never_holds_a_transaction(
    world, db, monkeypatch
):
    """The worker has not finished by the deadline: the answer says so with the progress and
    the link (not a list passed off as final), the wait is the 90 seconds in 3 second polls,
    and no transaction is held open while it sleeps (a held one blocks every audit writer)."""
    world.with_text(world.contract("A"))
    world.with_text(world.contract("B"))
    clock = Clock(monkeypatch, db)

    out = await query(world, wording=[WORDING])

    assert clock.sleeps == [3] * 30 and tr.QUERY_WAIT_SECONDS == 90
    assert clock.open_transactions == [False] * 30
    assert out["matches"] == [] and out["complete"] is False and out["confidence"] == "medium"
    assert out["reading"]["done"] == 0 and out["reading"]["contracts"] == 2
    assert out["summary"] == "Checked 0 of 2 contracts · 0 match · the AI is still reading 0 of 2 contracts"
    assert "Not final" in out["reading_note"] and "0 of 2" in out["reading_note"]
    assert out["review_url"].startswith("/tabular-reviews/")
    assert {u["reason"] for u in out["unknown"]} == {"still being read"}


async def test_the_same_question_repeated_does_not_start_a_second_paid_reading(world, db, monkeypatch):
    """The tool is MUTATING, so the runtime's replay guard hands back the first answer when the
    model (or a reconnect) repeats the call: one review, not two bills."""
    monkeypatch.setattr(tr, "has_permission", lambda *a, **k: True)
    world.with_text(world.contract("A"))
    Clock(monkeypatch, db)
    args = {"question": "Which renew?", "wording": [WORDING]}

    first = await tr.tool_runtime.execute(
        db, tool_name="query_contracts", tool_input=args, user=world.owner, session_id=world.session_id)
    again = await tr.tool_runtime.execute(
        db, tool_name="query_contracts", tool_input=args, user=world.owner, session_id=world.session_id)

    assert len(db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all()) == 1
    assert again["summary"] == first["summary"] and again["review_url"] == first["review_url"]


async def test_a_field_question_repeated_runs_fresh_so_a_new_contract_is_counted(world, db, monkeypatch):
    """The replay guard exists to stop a second PAID reading. A question that read nothing
    must not be replayed: "how many NDAs?" asked again after an upload kept saying the old
    number for ten minutes."""
    monkeypatch.setattr(tr, "has_permission", lambda *a, **k: True)
    world.contract("NDA 1", contract_type="NDA")
    args = {"question": "How many NDAs?", "contract_types": ["NDA"], "count_only": True}

    first = await tr.tool_runtime.execute(
        db, tool_name="query_contracts", tool_input=args, user=world.owner, session_id=world.session_id)
    world.contract("NDA 2", contract_type="NDA")
    again = await tr.tool_runtime.execute(
        db, tool_name="query_contracts", tool_input=args, user=world.owner, session_id=world.session_id)

    assert (first["matched"], again["matched"]) == (1, 2)


# --- deleted contracts and totals on the capped tools -----------------------------------------------------


def test_deleted_contracts_are_left_out_of_every_chat_list_and_count(world, db):
    """accessible_contract_filter has no deleted_at condition, so a deleted contract still
    came back from find_contracts, list_renewals, my_attention_items, list_obligations and the
    prompt's inventory (and was counted in "of M")."""
    today = utcnow().date()
    for title, deleted in (("Atlas live", None), ("Atlas gone", utcnow())):
        contract = world.contract(title, deleted_at=deleted)
        world.renewal(contract, notice_date=today + timedelta(days=2), renewal_window_starts_at=today)
        world.obligation(contract, today + timedelta(days=3))
    run = tr.tool_runtime

    found = run._find_contracts(db, payload=FindContractsInput(query="Atlas"), user=world.owner,
                                session_id=world.session_id)
    renewals = run._list_renewals(db, payload=ListRenewalsInput(), user=world.owner)
    attention = run._my_attention_items(db, payload=AttentionItemsInput(window_days=7), user=world.owner)
    obligations = run._list_obligations(db, payload=ListObligationsInput(), user=world.owner)
    inventory, total = ai_controller._contract_inventory(db, user=world.owner)

    assert [m["title"] for m in found["matches"]] == ["Atlas live"] and found["total"] == 1
    assert len(renewals["renewals"]) == 1 and renewals["total"] == 1
    assert {i["title"] for i in attention["items"]} == {"Atlas live"} and attention["total"] == 2
    assert obligations["count"] == 1
    assert [c["title"] for c in inventory] == ["Atlas live"] and total == 1


def test_capped_tools_say_how_many_there_are_beyond_what_they_show(world, db, monkeypatch):
    """find_contracts showed 8 and `found` said 8; list_renewals' count was the page size;
    my_attention_items and list_notices cut their rows with no total beside them. The model
    read each cut as the whole set."""
    today = utcnow().date()
    acme = [world.contract(f"Acme deal {i}") for i in range(12)]
    for contract in acme[:5]:
        world.renewal(contract, renewal_window_starts_at=today)
    for _ in range(45):
        world.obligation(acme[0], today + timedelta(days=1))
    run = tr.tool_runtime

    found = run._find_contracts(db, payload=FindContractsInput(query="Acme"), user=world.owner,
                                session_id=world.session_id)
    renewals = run._list_renewals(db, payload=ListRenewalsInput(limit=2), user=world.owner)
    attention = run._my_attention_items(db, payload=AttentionItemsInput(window_days=7), user=world.owner)
    from app.notices.service import NoticesService

    monkeypatch.setattr(NoticesService, "list_notices", lambda self, **kw: [{"id": i} for i in range(30)])
    notices = run._list_notices(db, payload=ListNoticesInput(), user=world.owner)

    assert (found["found"], len(found["matches"]), found["total"]) == (8, 8, 12)
    assert (renewals["count"], renewals["total"]) == (2, 5)
    assert (len(attention["items"]), attention["total"]) == (40, 45)
    assert (len(notices["notices"]), notices["total"]) == (25, 30)


# --- the Brain tool and the prompt ----------------------------------------------------------------------------


async def test_a_portfolio_search_answer_in_chat_is_marked_partial_and_not_left_high(world, db, monkeypatch):
    """ask_contract_brain answers from the best passages, yet its confidence read "high" for
    an answer built on 8 of 20 contracts. A portfolio-scope answer now says it did not look at
    every contract and is capped at medium."""
    passage = "Liability is capped at the fees paid."
    a, b = world.contract("A"), world.contract("B")
    monkeypatch.setattr(tr, "assemble_context", lambda *args, **kw: {
        "contract_ids": [a.id, b.id], "graph_facts": [], "context_text": passage, "source_count": 1})

    async def answer(*args, **kw):
        return BrainAnswerOutput(answer="Capped at fees.", confidence="high",
                                 citations=[CitationInput(quote=passage)])

    monkeypatch.setattr(ai_controller, "run_structured_skill", answer)

    out = await tr.tool_runtime._ask_contract_brain(
        db, payload=BrainAskInput(question="Which contracts cap liability?", query_scope="portfolio"),
        user=world.owner, session_id=world.session_id)

    assert out["confidence"] == "medium"
    assert "best-matching passages, not every contract" in out["limitations"]


def test_the_saved_chat_block_keeps_the_table_link():
    """After a reload the chat is rebuilt from the saved blocks, which kept only a few keys:
    the summary and the link to the table the AI was still filling were lost."""
    blocks: list[dict] = []
    accumulate_block(blocks, {"event": "tool_started", "payload": {"tool_name": "query_contracts"}})
    accumulate_block(blocks, {"event": "tool_finished", "payload": {
        "tool_name": "query_contracts",
        "result": {"summary": "Checked 3 of 3 contracts · 2 match", "review_url": "/tabular-reviews/r-1",
                   "matches": [{"title": "A"}]}}})

    assert blocks[0]["artifact"] == {"summary": "Checked 3 of 3 contracts · 2 match",
                                     "review_url": "/tabular-reviews/r-1"}


def test_the_prompt_never_presents_the_capped_inventory_as_the_whole_portfolio():
    """The 60-newest list was headed "The user's contract portfolio", so the model counted
    from it. The heading now says how many are shown of how many, and where counts come from."""
    kwargs = dict(message="How many NDAs?", contract_id=None, contract_ids=[], handles=[])
    rows = [{"contract_id": f"c-{i}", "title": f"T{i}", "counterparty": None, "stage": "active"}
            for i in range(3)]

    capped = ai_controller._assistant_user_prompt(**kwargs, contract_inventory=rows, contract_total=200)
    whole = ai_controller._assistant_user_prompt(**kwargs, contract_inventory=rows, contract_total=3)

    assert "The 3 most recent of the user's 200 contracts (not the full list" in capped
    assert "use query_contracts for counts and lists" in capped
    assert "most recent" not in whole and "The user's 3 contracts" in whole


async def test_how_many_sends_the_model_counts_not_lists_and_long_lists_are_shortened(world, monkeypatch):
    """Guards a whole book's list going into the model's context (and being re-paid every
    turn): "how many" gets counts only, and a list is cut to QUERY_LIST_LIMIT with the rest
    counted, never silently."""
    for title in ("A", "B", "C"):
        world.contract(title)
    monkeypatch.setattr(tr, "QUERY_LIST_LIMIT", 2)

    counted = await query(world, count_only=True)
    listed = await query(world)

    assert counted["matched"] == 3 and counted["matches"] == [] and counted["matches_omitted"] == 3
    assert [m["title"] for m in listed["matches"]] == ["A", "B"] and listed["matches_omitted"] == 1
    assert "full lists are on the Contract Brain page" in listed["list_note"]


def test_the_tool_schema_tells_the_model_about_lacks_and_not_checkable():
    """Guards the chat putting "no X clause" in wording (the reader then says yes with nothing
    to quote, and the very contracts asked for land in "could not check")."""
    from app.ai.tool_registry import QueryContractsInput, tool_registry

    props = QueryContractsInput.model_json_schema()["properties"]
    assert "presence question" in props["lacks"]["description"]
    assert "today" in props["not_checkable"]["description"]
    assert "`lacks`" in tool_registry.get("query_contracts").description
