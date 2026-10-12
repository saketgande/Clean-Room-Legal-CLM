"""Complete answers to "which / how many contracts" questions (Step 3e).

A portfolio question used to be answered from the top few retrieved passages: it named 8 of
20 matching contracts and called itself "high confidence". The model now only proposes a typed
filter; code runs it over EVERY contract the user can open and says what it checked. These
tests run the real filter against Postgres inside one rolled-back transaction, in an
organisation of their own, so the dev database's rows never change the counts. No model is
called: the skill is faked and the table review's jobs go to an in-memory broker nobody reads.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.registry import skill_registry
from app.ai.schemas import (
    BrainAnswerOutput,
    CitationInput,
    DateRange,
    PortfolioFilter,
    PortfolioFilterOutput,
    TabularCellOutput,
    TabularRowOutput,
)
from app.auth.models import User
from app.contract_brain import portfolio, routes
from app.contract_brain.models import BrainQuery
from app.contract_brain.portfolio import (
    BARE_ALL_RE,
    answer_text,
    bare_all_filter,
    looks_like_list_question,
    mark_search_partial,
    portfolio_result,
    run_portfolio_filter,
)
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine, utcnow
from app.integrations._claude_mock import structured_payload_by_tool
from app.organizations.models import Organization
from app.tabular_review.models import TabularReview, TabularReviewCell, TabularReviewColumn
from app.walls.models import EthicalWall, EthicalWallPrincipal

TODAY = date(2026, 10, 8)


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
    session = Session(bind=conn)
    yield session
    session.close()


class World:
    """One organisation with an owner; contracts are the owner's unless said otherwise."""

    def __init__(self, db: Session):
        self.db = db
        org = Organization(name="Portfolio Co", slug=f"portfolio-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.owner = self.user("owner")

    def user(self, label: str) -> User:
        user = User(org_id=self.org_id, email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
                    full_name=label, hashed_password="hash", status="active")
        self.db.add(user)
        self.db.flush()
        return user

    def contract(self, title: str = "Contract", *, owner: User | None = None, **fields) -> Contract:
        contract = Contract(org_id=self.org_id, title=title,
                            owner_user_id=(owner or self.owner).id, **fields)
        self.db.add(contract)
        self.db.flush()
        return contract

    def with_text(self, contract: Contract, text: str = "Each party keeps the other's information secret.") -> Contract:
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
        snap = ContractTextSnapshot(org_id=self.org_id, contract_id=contract.id, contract_version_id=version.id,
                                    extraction_method="text", text=text)
        db.add(snap)
        db.flush()
        version.text_snapshot_id = snap.id
        contract.current_authoritative_version_id = version.id
        db.flush()
        return contract


@pytest.fixture
def world(db, monkeypatch):
    # These users carry no roles; reading spends money and needs the AI tools permission,
    # which the test of that gate takes away again.
    monkeypatch.setattr(portfolio, "has_permission", lambda perms, needed: True)
    return World(db)


def run(world: World, flt: PortfolioFilter, *, user: User | None = None, **kw) -> dict:
    kw.setdefault("start_reading", False)
    return run_portfolio_filter(world.db, user=user or world.owner, flt=flt, question="q", today=TODAY, **kw)


def titles(items: list[dict]) -> list[str]:
    return [i["title"] for i in items]


# --- unknown is never "no" ---------------------------------------------------------------------


def test_a_blank_field_is_listed_as_unknown_never_counted_as_no(world):
    """A contract with no expiry date used to vanish from "which expire in 2027" as if it
    did not match. It must be reported as unchecked, with the reason, and keep the answer
    from calling itself complete."""
    world.contract("A", expiration_date=date(2027, 3, 31))
    world.contract("B", expiration_date=date(2026, 5, 1))
    world.contract("C")

    r = run(world, PortfolioFilter(expires=DateRange(year=2027)))

    assert titles(r["matches"]) == ["A"]
    assert titles(r["unknown"]) == ["C"]
    assert r["unknown"][0]["reason"] == "no expiry date recorded"
    assert r["unknown_reasons"] == {"no expiry date recorded": 1}
    assert (r["total"], r["checked"], r["matched"]) == (3, 2, 1)
    assert r["complete"] is False and r["confidence"] == "medium"
    assert set(r["contract_ids"]) == {m["contract_id"] for m in r["matches"] + r["unknown"]}


def test_every_contract_known_gives_a_complete_high_confidence_answer(world):
    """Confidence is set by code: high only when every contract got a definite answer."""
    world.contract("A", contract_type="NDA")
    world.contract("B", contract_type="MSA")

    r = run(world, PortfolioFilter(contract_types=["NDA"]))

    assert (r["total"], r["checked"], r["matched"]) == (2, 2, 1)
    assert r["complete"] is True and r["confidence"] == "high"
    assert r["answer_mode"] == "database_filter"


def test_a_match_says_where_each_value_came_from(world):
    """The "why" line carries the field's provenance, so a person can tell a date a form
    gave from one the AI read."""
    world.contract("A", expiration_date=date(2027, 3, 31),
                   metadata_json={"field_sources": {"expiration_date": "form"}})
    world.contract("B", expiration_date=date(2027, 4, 1),
                   metadata_json={"field_sources": {"expiration_date": "ai"}})

    r = run(world, PortfolioFilter(expires=DateRange(year=2027)))

    assert r["matches"][0]["why"] == ["expires 31 Mar 2027 (request form)"]
    assert r["matches"][1]["why"] == ["expires 1 Apr 2027 (AI reading)"]
    assert r["conditions"] == ["expires 1 Jan 2027 – 31 Dec 2027"]


# --- who and what is in the denominator -------------------------------------------------------


def test_deleted_contracts_are_not_counted(world):
    """A soft-deleted contract is not in the portfolio; it must not inflate M."""
    world.contract("Live")
    world.contract("Gone", deleted_at=utcnow())

    assert run(world, PortfolioFilter())["total"] == 1


def test_a_non_admin_is_checked_over_only_what_they_can_open(world):
    """M is the user's own accessible set (owner / creator / grant), so a lawyer is never
    told about, or counted against, contracts they cannot open."""
    other = world.user("other")
    world.contract("Mine")
    world.contract("Theirs", owner=other)

    r = run(world, PortfolioFilter())

    assert titles(r["matches"]) == ["Mine"] and r["total"] == 1
    assert run(world, PortfolioFilter(), user=other)["total"] == 1


def test_an_admin_sees_the_whole_organisation_and_no_other(world, monkeypatch):
    """Admins widen within their organisation only."""
    monkeypatch.setattr("app.contracts.access.is_org_admin", lambda user: True)
    other = world.user("other")
    world.contract("Mine")
    world.contract("Theirs", owner=other)
    foreign = World(world.db)
    foreign.contract("Other org")

    assert run(world, PortfolioFilter())["total"] == 2


def test_an_ethical_wall_removes_the_contract_from_the_check(world):
    """Walls bind everyone; a walled contract is neither matched nor listed as unchecked."""
    walled = world.contract("Walled")
    world.contract("Open")
    wall = EthicalWall(org_id=world.org_id, name="w", scope_type="contract", scope_id=walled.id, active=True)
    world.db.add(wall)
    world.db.flush()
    world.db.add(EthicalWallPrincipal(org_id=world.org_id, wall_id=wall.id, principal_type="user",
                                      principal_id=world.owner.id))
    world.db.flush()

    r = run(world, PortfolioFilter())

    assert titles(r["matches"]) == ["Open"] and r["total"] == 1


# --- individual conditions -----------------------------------------------------------------------


def test_a_value_in_another_currency_is_unknown_not_a_miss(world):
    """Rupees and dollars are never compared: the dollar contract could be above the bar or
    below it, so it is unchecked."""
    world.contract("Rupees", value_amount=5_000_000, currency="INR")
    world.contract("Dollars", value_amount=9_000_000, currency="USD")
    world.contract("Small", value_amount=10, currency="INR")
    world.contract("No value")

    r = run(world, PortfolioFilter(value_min=1_000_000, currency="inr"))

    assert titles(r["matches"]) == ["Rupees"]
    assert r["unknown_reasons"] == {"value in USD, not INR": 1, "no value recorded": 1}
    assert r["conditions"] == ["value at least 1,000,000 INR"]


def test_a_value_bound_without_a_currency_leaves_every_contract_unknown(world):
    """"Over a million" with no currency cannot be run honestly. Dropping it would leave an
    empty filter, and every contract would "match": instead each one is unknown, and why."""
    world.contract("A", value_amount=1, currency="INR")

    r = run(world, PortfolioFilter(value_min=1_000_000))

    assert r["matched"] == 0 and r["checked"] == 0 and r["complete"] is False
    assert r["unknown_reasons"] == {"the value was given without a currency": 1}


def test_a_condition_that_cannot_be_run_never_turns_into_every_contract_matching(world):
    """Guards the reviewer's case: a governing law that names no place used to be dropped,
    and the answer then listed the whole portfolio as matches under "complete"."""
    world.contract("A", jurisdiction="India")
    world.contract("B", jurisdiction="Delaware")

    r = run(world, PortfolioFilter(governing_law="laws"))

    assert r["matched"] == 0 and titles(r["unknown"]) == ["A", "B"]
    assert r["confidence"] == "medium" and r["complete"] is False


def test_a_place_named_with_filler_or_inside_another_place_is_read_as_that_place(world):
    """Guards the re-review's cases: "State of Delaware" missed every contract recorded
    "Delaware", "England & Wales" missed "England and Wales", and "Wales" was found in New
    South Wales and "British" (= UK) in British Columbia, all under "complete"."""
    world.contract("Delaware", jurisdiction="Delaware")
    world.contract("Delaware USA", jurisdiction="Delaware, USA")
    world.contract("London", jurisdiction="England and Wales")
    world.contract("Sydney", jurisdiction="New South Wales, Australia")
    world.contract("Vancouver", jurisdiction="Province of British Columbia")

    assert titles(run(world, PortfolioFilter(governing_law="State of Delaware"))["matches"]) == [
        "Delaware", "Delaware USA"]
    assert titles(run(world, PortfolioFilter(governing_law="England & Wales"))["matches"]) == ["London"]
    assert titles(run(world, PortfolioFilter(governing_law="UK"))["matches"]) == ["London"]


def test_a_country_alone_cannot_settle_a_question_about_one_of_its_states(world):
    """"US" neither is nor rules out Delaware: unknown, not a definite no."""
    world.contract("Only US", jurisdiction="US")
    world.contract("Texas", jurisdiction="Texas")

    r = run(world, PortfolioFilter(governing_law="Delaware"))

    assert r["matched"] == 0 and titles(r["unknown"]) == ["Only US"]
    assert r["unknown"][0]["reason"] == "governing law recorded only as “US”"


def test_an_amendment_is_not_the_agreement_it_amends_and_a_qualifier_must_be_recorded(world):
    """"How many MSAs" must not count their amendments, and "Mutual NDA" cannot be told
    from a type that only says "NDA"."""
    world.contract("MSA", contract_type="MSA")
    world.contract("MSA amendment", contract_type="Amendment No. 3 to MSA")
    world.contract("Mutual", contract_type="Mutual NDA")
    world.contract("Plain", contract_type="NDA")

    assert titles(run(world, PortfolioFilter(contract_types=["MSA"]))["matches"]) == ["MSA"]
    mutual = run(world, PortfolioFilter(contract_types=["Mutual NDA"]))
    assert titles(mutual["matches"]) == ["Mutual"] and titles(mutual["unknown"]) == ["Plain"]


def test_calendar_periods_are_counted_by_code(world):
    """"This quarter" and "the next 6 months" are spans code works out from today (8 Oct
    2026): the model only names the period."""
    world.contract("Q4", expiration_date=date(2026, 12, 31))
    world.contract("Jan", expiration_date=date(2027, 1, 1))
    world.contract("April", expiration_date=date(2027, 4, 8))

    assert titles(run(world, PortfolioFilter(expires=DateRange(period="this_quarter")))["matches"]) == ["Q4"]
    assert titles(run(world, PortfolioFilter(expires=DateRange(period="next_quarter")))["matches"]) == ["Jan"]
    six = run(world, PortfolioFilter(expires=DateRange(within_next_months=6)))
    assert titles(six["matches"]) == ["April", "Jan", "Q4"]
    assert six["conditions"] == ["expires 8 Oct 2026 – 8 Apr 2027"]


def test_what_the_model_could_not_express_is_named_and_the_answer_is_not_complete(world):
    world.contract("A", contract_type="NDA")

    r = run(world, PortfolioFilter(contract_types=["NDA"], not_checkable=["notice deadline this quarter"]))

    assert r["not_checked"] == ["notice deadline this quarter"]
    assert r["complete"] is False and r["confidence"] == "medium"


def test_dates_are_counted_by_code_from_the_year_or_the_next_n_days(world):
    """The model never counts days: "in the next 90 days" runs from code's today, and both
    ends of a from/to range are inclusive."""
    world.contract("Soon", expiration_date=date(2026, 12, 1))     # 54 days away
    world.contract("Edge", expiration_date=date(2027, 1, 6))      # exactly today + 90
    world.contract("Later", expiration_date=date(2027, 1, 7))
    world.contract("Past", expiration_date=date(2026, 9, 1))

    soon = run(world, PortfolioFilter(expires=DateRange(within_next_days=90)))
    assert titles(soon["matches"]) == ["Edge", "Soon"]

    span = run(world, PortfolioFilter(expires=DateRange(from_date=date(2026, 12, 1), to_date=date(2027, 1, 6))))
    assert titles(span["matches"]) == ["Edge", "Soon"]
    assert span["conditions"] == ["expires 1 Dec 2026 – 6 Jan 2027"]


def test_a_contract_type_is_matched_through_its_aliases(world):
    """"NDA" must find "Mutual Non-Disclosure Agreement" and not an MSA; a contract with no
    type, or one that names no kind ("Agreement"), is unchecked, not a miss."""
    world.contract("A", contract_type="Mutual Non-Disclosure Agreement")
    world.contract("B", contract_type="nda")
    world.contract("C", contract_type="MSA")
    world.contract("D")
    world.contract("E", contract_type="Agreement")

    r = run(world, PortfolioFilter(contract_types=["NDAs"]))

    assert titles(r["matches"]) == ["A", "B"]
    assert titles(r["unknown"]) == ["D", "E"]
    assert "no contract type recorded" in r["unknown_reasons"]


def test_a_shared_generic_word_never_makes_one_kind_of_agreement_another(world):
    """Guards the old token overlap, where the alias words "services", "data" and "master"
    made a DPA an MSA and a services agreement an SOW, under "complete"."""
    world.contract("MSA", contract_type="Master Services Agreement")
    world.contract("Services (MSA)", contract_type="Services (MSA)")
    world.contract("DPA", contract_type="Data Processing Agreement")
    world.contract("Data services", contract_type="Data Services Agreement")
    world.contract("SOW", contract_type="Statement of Work")

    assert titles(run(world, PortfolioFilter(contract_types=["MSA"]))["matches"]) == ["MSA", "Services (MSA)"]
    assert titles(run(world, PortfolioFilter(contract_types=["DPA"]))["matches"]) == ["DPA"]
    assert titles(run(world, PortfolioFilter(contract_types=["statement of work"]))["matches"]) == ["SOW"]


def test_a_type_that_names_no_kind_of_agreement_is_no_condition(world):
    """"Which agreements ..." puts no type condition on the list."""
    world.contract("A", contract_type="NDA")

    r = run(world, PortfolioFilter(contract_types=["agreements"]))

    assert r["matched"] == 1 and r["conditions"] == [] and r["complete"] is True


def test_a_counterparty_is_matched_by_its_normalised_name(world):
    """"Acme" finds "ACME, Inc." and "Acme Industries Ltd"; a placeholder name such as "the
    Counterparty" is unchecked, not a miss."""
    world.contract("A", counterparty_name="ACME, Inc.")
    world.contract("B", counterparty_name="Acme Industries Ltd")
    world.contract("C", counterparty_name="Zenith Ltd")
    world.contract("D", counterparty_name="the Counterparty")

    r = run(world, PortfolioFilter(counterparty="Acme"))

    assert titles(r["matches"]) == ["A", "B"]
    assert titles(r["unknown"]) == ["D"]


def test_governing_law_matches_whole_words_only(world):
    """"India" must not find Indiana, and "England and Wales" must find "England & Wales"."""
    world.contract("A", jurisdiction="Republic of India")
    world.contract("B", jurisdiction="Indiana")
    world.contract("C", jurisdiction="England & Wales")
    world.contract("D")

    assert titles(run(world, PortfolioFilter(governing_law="India"))["matches"]) == ["A"]
    r = run(world, PortfolioFilter(governing_law="England and Wales"))
    assert titles(r["matches"]) == ["C"]
    assert titles(r["unknown"]) == ["D"]
    assert r["unknown"][0]["reason"] == "no governing law recorded"


def test_a_country_covers_the_places_a_contract_names_instead(world):
    """Guards "UK" / "US" being emptied by the old normaliser (so every contract read as "no
    governing law recorded") and "English law" being a definite no for England and Wales."""
    world.contract("London", jurisdiction="England and Wales")
    world.contract("Edinburgh", jurisdiction="Scotland")
    world.contract("Wilmington", jurisdiction="State of Delaware")
    world.contract("New York", jurisdiction="Laws of the State of New York")
    world.contract("Short UK", jurisdiction="UK")
    world.contract("English", jurisdiction="English law")

    assert titles(run(world, PortfolioFilter(governing_law="UK"))["matches"]) == [
        "Edinburgh", "English", "London", "Short UK"]
    assert titles(run(world, PortfolioFilter(governing_law="US"))["matches"]) == ["New York", "Wilmington"]
    assert titles(run(world, PortfolioFilter(governing_law="English law"))["matches"]) == ["English", "London"]


def test_stage_and_risk_band_use_the_columns_and_unreviewed_is_a_known_band(world):
    """A NULL risk band means "not yet reviewed": that IS the "unknown" band, a real answer."""
    world.contract("A", lifecycle_stage="active", risk_band="high")
    world.contract("B", lifecycle_stage="active")
    world.contract("C", lifecycle_stage="review", risk_band="low")

    r = run(world, PortfolioFilter(stages=["active"], risk_bands=["unknown", "high"]))

    assert titles(r["matches"]) == ["A", "B"] and r["complete"] is True
    assert r["matches"][1]["why"] == ["stage: active", "risk: unknown"]


# --- reading the contract's own words -------------------------------------------------------------


def wording_filter(*questions: str, **kw) -> PortfolioFilter:
    return PortfolioFilter(wording=list(questions) or ["Does this contract renew automatically?"], **kw)


def test_wording_starts_one_review_over_only_the_contracts_that_met_the_other_conditions(world, db):
    """Reading is the costly step, so it is spent once, as one table review with a yes/no
    column per question, on the contracts the fields did not already rule out."""
    nda = world.with_text(world.contract("NDA 1", contract_type="NDA"))
    world.with_text(world.contract("MSA 1", contract_type="MSA"))
    world.contract("NDA without a document", contract_type="NDA")

    r = run(world, wording_filter("Does this contract renew automatically?",
                                  "Does this contract allow termination for convenience?",
                                  contract_types=["NDA"]), start_reading=True)

    [review] = db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all()
    columns = db.scalars(select(TabularReviewColumn).where(
        TabularReviewColumn.tabular_review_id == review.id).order_by(TabularReviewColumn.position)).all()
    cells = db.scalars(select(TabularReviewCell).where(TabularReviewCell.tabular_review_id == review.id)).all()
    assert review.metadata_json == {"source": "portfolio_question", "question": "q"}
    assert [c.name for c in columns] == ["Does this contract renew automatically?",
                                         "Does this contract allow termination for convenience?"]
    assert all(c.metadata_json == {"answer_type": "yes_no", "wanted": "yes"} and "yes/no question" in c.prompt
               for c in columns)
    assert {c.contract_id for c in cells} == {nda.id} and len(cells) == 2
    assert r["reading"] == {"tabular_review_id": review.id, "contracts": 1, "done": 0, "failed": 0,
                            "estimate_usd": r["reading"]["estimate_usd"], "started": True}
    assert r["reading"]["estimate_usd"] > 0
    assert r["matches"] == [] and r["complete"] is False
    assert {u["title"]: u["reason"] for u in r["unknown"]} == {
        "NDA 1": "still being read", "NDA without a document": "no document to read"}
    assert r["conditions"][-2:] == ["AI reads each contract: Does this contract renew automatically?",
                                    "AI reads each contract: Does this contract allow termination for convenience?"]


def test_the_per_question_limit_is_named_not_silently_applied(world, monkeypatch):
    """Past the table review's contract limit the rest are reported as not read, and the
    limit is named in what was not checked."""
    monkeypatch.setattr(portfolio, "MAX_REVIEW_CONTRACTS", 2)
    for title in ("A", "B", "C", "D"):
        world.with_text(world.contract(title))

    r = run(world, wording_filter(), start_reading=True)

    assert r["reading"]["contracts"] == 2
    assert {u["title"]: u["reason"] for u in r["unknown"] if u["title"] in ("C", "D")} == {
        "C": "not read: 2-contract limit per question", "D": "not read: 2-contract limit per question"}
    assert any("2-contract limit per question" in n for n in r["not_checked"])
    assert r["complete"] is False


def test_a_dry_run_says_reading_was_not_started_and_creates_no_review(world, db):
    world.with_text(world.contract("A"))

    r = run(world, wording_filter())

    assert r["reading"]["started"] is False and r["reading"]["tabular_review_id"] is None
    assert "the AI reading was not started" in r["not_checked"]
    assert db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all() == []
    assert titles(r["unknown"]) == ["A"] and r["unknown"][0]["reason"] == "reading was not started"


RENEWS = "This Agreement renews automatically for successive one-year terms."


def settle(db, review_id: str, contract_id: str, *, status="complete", verdict=None, quote=RENEWS) -> None:
    cell = db.scalar(select(TabularReviewCell).where(
        TabularReviewCell.tabular_review_id == review_id, TabularReviewCell.contract_id == contract_id))
    cell.status = status
    cell.raw_ai_output = {"verdict": verdict} if verdict else {}
    cell.citations = [{"quote": quote}] if quote else []
    db.flush()


def read_four(world, db, flt: PortfolioFilter | None = None):
    text = f"{RENEWS} Each party keeps the other's information secret."
    contracts = {t: world.with_text(world.contract(t), text) for t in ("Yes", "No", "Unquoted", "Broken", "Waiting")}
    r = run(world, flt or wording_filter(), start_reading=True)
    return contracts, r["reading"]["tabular_review_id"], r


def test_the_list_is_decided_from_the_typed_verdicts_not_the_prose(world, db):
    """Code decides: a yes whose quote is in the contract matches; no / not-stated is a definite
    miss; a yes with no quote, one that failed, and one still running are unchecked."""
    contracts, review_id, first = read_four(world, db)
    assert first["matched"] == 0 and first["reading"]["done"] == 0
    settle(db, review_id, contracts["Yes"].id, verdict="yes")
    settle(db, review_id, contracts["No"].id, verdict="no")
    settle(db, review_id, contracts["Unquoted"].id, status="needs_review", verdict="yes", quote="")
    settle(db, review_id, contracts["Broken"].id, status="failed")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert titles(r["matches"]) == ["Yes"]
    assert r["matches"][0]["why"] == [f"Does this contract renew automatically? Yes — “{RENEWS}”"]
    assert {u["title"]: u["reason"] for u in r["unknown"]} == {
        "Unquoted": "the AI said yes but its quote is not in the contract — see the table",
        "Broken": "the AI could not read this contract",
        "Waiting": "still being read"}
    assert (r["total"], r["checked"], r["matched"]) == (5, 2, 1)
    assert r["reading"]["contracts"] == 5 and r["reading"]["done"] == 4 and r["reading"]["failed"] == 1
    assert r["confidence"] == "medium"


def test_a_finished_reading_with_every_cell_decided_is_complete(world, db):
    contracts, review_id, first = read_four(world, db)
    for title, c in contracts.items():
        settle(db, review_id, c.id, verdict="yes" if title == "Yes" else "not_stated")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert titles(r["matches"]) == ["Yes"] and r["unknown"] == []
    assert r["complete"] is True and r["confidence"] == "high"
    assert portfolio.reading_finished(r)


def test_a_yes_whose_quote_is_not_in_the_contract_is_never_a_match(world, db):
    """Guards taking a typed yes on the model's word: the job keeps any quote it writes, so a
    made-up quote must be checked against the contract's own text before it counts."""
    contracts, review_id, first = read_four(world, db)
    settle(db, review_id, contracts["Yes"].id, verdict="yes", quote="The Supplier shall pay a penalty of one million pounds.")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert r["matched"] == 0
    assert {u["title"]: u["reason"] for u in r["unknown"]}["Yes"].startswith("the AI said yes but its quote")


def test_a_one_word_quote_never_verifies_a_yes_but_an_elided_one_does(world, db):
    """A single word ("automatically") is found in almost any contract, so it shows nothing;
    a quote that skips the middle with "..." still points at real words."""
    contracts, review_id, first = read_four(world, db)
    settle(db, review_id, contracts["Yes"].id, verdict="yes", quote="automatically")
    settle(db, review_id, contracts["No"].id, verdict="yes",
           quote="This Agreement renews automatically ... the other's information secret.")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert titles(r["matches"]) == ["No"]
    assert {u["title"]: u["reason"] for u in r["unknown"]}["Yes"].startswith("the AI said yes but its quote")


def test_a_no_on_a_text_longer_than_the_ai_reads_is_not_an_answer(world, db, monkeypatch):
    """The AI reads the first MAX_FULL_TEXT_CHARS: "not found" past that proves nothing, so
    the contract is unknown; a quoted yes still counts."""
    monkeypatch.setattr(portfolio, "MAX_FULL_TEXT_CHARS", 20)
    contracts, review_id, first = read_four(world, db)
    settle(db, review_id, contracts["Yes"].id, verdict="yes")
    settle(db, review_id, contracts["No"].id, verdict="not_stated", quote="")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert titles(r["matches"]) == ["Yes"]
    assert {u["title"]: u["reason"] for u in r["unknown"]}["No"].startswith("too long to read in full")


def test_a_column_a_person_adds_to_the_table_never_changes_the_answer(world, db):
    """The answer links to the table, where columns can be added: only the filter's own
    yes/no columns decide, or a finished, saved list would turn back into "unknown"."""
    contracts, review_id, first = read_four(world, db)
    db.add(TabularReviewColumn(org_id=world.org_id, tabular_review_id=review_id, name="Governing law",
                               prompt="Which law governs?", position=5))
    db.flush()
    for title, c in contracts.items():
        settle(db, review_id, c.id, verdict="yes" if title == "Yes" else "no")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert titles(r["matches"]) == ["Yes"] and r["unknown"] == [] and r["complete"] is True


def test_asking_again_follows_the_reading_already_under_way_at_no_new_cost(world, db):
    """Guards a re-ask (a chat retry, "is it done?") starting a second paid reading of the
    same contracts for the same question."""
    world.with_text(world.contract("A"))
    first = run(world, wording_filter(), start_reading=True)
    again = run(world, wording_filter(), start_reading=True)

    assert again["reading"]["tabular_review_id"] == first["reading"]["tabular_review_id"]
    assert again["reading"]["estimate_usd"] == 0.0
    assert len(db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all()) == 1
    other = run(world, wording_filter("Does this contract allow termination for convenience?"), start_reading=True)
    assert other["reading"]["tabular_review_id"] != first["reading"]["tabular_review_id"]


def test_a_reading_larger_than_todays_ai_budget_is_not_started(world, db, monkeypatch):
    """Started anyway it would use up the org's daily cap and stop every other AI feature
    until tomorrow."""
    monkeypatch.setattr(portfolio, "tokens_left", lambda org_id: 10)
    world.with_text(world.contract("A"))

    r = run(world, wording_filter(), start_reading=True)

    assert r["reading"]["started"] is False
    assert any("more than what is left of today's AI budget" in n for n in r["not_checked"])
    assert db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all() == []


def test_a_lacks_question_matches_on_no_and_never_on_an_unchecked_yes(world, db):
    """Guards "which contracts have NO limitation of liability clause" dropping the very
    contracts asked for: the presence question is asked, and a no or not-stated (which has
    nothing to quote, so the cell has no citation) is the match."""
    flt = PortfolioFilter(lacks=["Does this contract renew automatically?"])
    contracts, review_id, first = read_four(world, db, flt)
    settle(db, review_id, contracts["Yes"].id, verdict="yes")                        # has it: out
    settle(db, review_id, contracts["No"].id, status="needs_review", verdict="no", quote="")
    settle(db, review_id, contracts["Unquoted"].id, verdict="not_stated", quote="")
    settle(db, review_id, contracts["Broken"].id, verdict="yes", quote="Something it never says.")
    settle(db, review_id, contracts["Waiting"].id, status="failed")

    r = portfolio_result(db, user=world.owner, meta=first)

    assert titles(r["matches"]) == ["No", "Unquoted"]
    assert r["matches"][0]["why"] == [
        "Does this contract renew automatically? No — not found in the contract (the AI read all of it)"]
    assert titles(r["unknown"]) == ["Broken", "Waiting"]
    assert first["conditions"] == ["AI reads each contract: Does this contract renew automatically? — must be no"]


def test_reading_needs_the_ai_tools_permission(world, db, monkeypatch):
    """Guards a member with only assistant:use starting a paid reading of up to 100
    contracts, which a table review by hand refuses them."""
    monkeypatch.setattr(portfolio, "has_permission", lambda perms, needed: needed != "assistant:use_ai_tools")
    world.with_text(world.contract("A"))

    r = run(world, wording_filter(), start_reading=True)

    assert r["reading"]["started"] is False
    assert "the AI reading needs the AI tools permission (ask an admin)" in r["not_checked"]
    assert db.scalars(select(TabularReview).where(TabularReview.org_id == world.org_id)).all() == []


def test_starting_a_reading_is_audited(world, db):
    from app.core.models import AuditLog

    world.with_text(world.contract("A"))
    r = run(world, wording_filter(), start_reading=True)

    [row] = db.scalars(select(AuditLog).where(AuditLog.resource_id == r["reading"]["tabular_review_id"])).all()
    assert row.action == "contract_brain.portfolio_reading_started"
    assert row.actor_user_id == world.owner.id and row.after["contracts"] == 1


def test_a_yes_that_is_not_a_complete_cell_is_never_a_match(world, db):
    """A "yes" from a cell that is still running must not count; only a finished cell does."""
    contracts, review_id, first = read_four(world, db)
    settle(db, review_id, contracts["Yes"].id, status="running", verdict="yes")

    assert portfolio_result(db, user=world.owner, meta=first)["matched"] == 0


def test_the_result_drops_contracts_the_viewer_cannot_open(world, db):
    """Rebuilt for someone else, the lists, the reading and the totals cover only what they
    may open; the stored answer is the same one the asker saw."""
    contracts, review_id, first = read_four(world, db)
    settle(db, review_id, contracts["Yes"].id, verdict="yes")
    settle(db, review_id, contracts["No"].id, verdict="yes")
    other = world.user("other")
    contracts["No"].owner_user_id = other.id
    db.flush()

    seen_by_other = portfolio_result(db, user=other, meta=first)

    assert titles(seen_by_other["matches"]) == ["No"] and seen_by_other["total"] == 1
    assert seen_by_other["unknown"] == [] and seen_by_other["reading"]["contracts"] == 1
    assert titles(portfolio_result(db, user=world.owner, meta=first)["matches"]) == ["Yes"]


# --- /ask routing ----------------------------------------------------------------------------------


@pytest.fixture
def ai(monkeypatch):
    """The model, faked: which skills were called, and what each returns (or raises)."""
    state = SimpleNamespace(calls=[], outputs={})

    async def fake(_db, *, skill_name, **_kw):
        state.calls.append(skill_name)
        out = state.outputs[skill_name]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(routes.ai_controller, "run_structured_skill", fake)
    return state


async def ask(world, question: str, scope: str = "portfolio"):
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-1"))
    return await routes.ask_contract_brain(
        routes.BrainAskRequest(question=question, query_scope=scope), request, world.db, world.owner)


async def test_a_bare_count_needs_no_model_call(world, ai):
    """"How many contracts?" has no condition to propose, so it is answered from the
    database with no AI call at all."""
    world.contract("A")
    world.contract("B")

    saved = await ask(world, "How many contracts do we have?")

    assert ai.calls == []
    meta = saved.retrieval_metadata
    assert meta["answer_mode"] == "database_filter" and meta["total"] == 2 and meta["count_only"] is True
    assert meta["confidence"] == "high" and "citation_review" in meta and meta["scope"] == "portfolio"
    assert saved.answer.startswith("**2 contracts**")


async def test_a_list_question_makes_exactly_one_filter_call_and_runs_it_over_everything(world, ai):
    """One model call turns the question into a filter; the answer, the list and the
    confidence all come from code."""
    world.contract("A", contract_type="NDA")
    world.contract("B", contract_type="MSA")
    ai.outputs["portfolio_filter"] = PortfolioFilterOutput(is_list_question=True, contract_types=["NDA"])

    saved = await ask(world, "Which NDAs do we have?")

    assert ai.calls == ["portfolio_filter"]
    meta = saved.retrieval_metadata
    assert meta["answer_mode"] == "database_filter" and meta["matched"] == 1 and meta["total"] == 2
    assert [m["title"] for m in meta["matches"]] == ["A"]
    assert meta["contract_ids"] == [m["contract_id"] for m in meta["matches"]]
    assert "is_list_question" not in meta["filter"]
    assert saved.answer.startswith("**1 of 2 contracts match:** type: NDA.")
    assert saved.citations == []


async def test_a_proposed_filter_with_no_condition_is_never_a_complete_list(world, ai):
    """Guards the model dropping every condition of a real question ("with our biggest
    customers"): the whole portfolio would come back as "N of N match, complete"."""
    world.contract("A")
    ai.outputs["portfolio_filter"] = PortfolioFilterOutput(is_list_question=True)

    saved = await ask(world, "Which contracts are with our biggest customers?")

    meta = saved.retrieval_metadata
    assert meta["not_checked"] == ["the question's conditions could not be turned into a filter"]
    assert meta["complete"] is False and meta["confidence"] == "medium"


async def test_a_question_that_is_not_a_list_question_goes_to_search_marked_partial(world, ai, monkeypatch):
    """The model says it is not a list question: search answers it, and because search only
    looked at the best passages the answer says so and is not left at "high"."""
    contract = world.contract("A")
    world.contract("B")
    passage = "Liability is capped at the fees paid."
    monkeypatch.setattr(routes, "hybrid_sources", lambda *a, **k: {
        "semantic": [{"contract_id": contract.id, "contract_title": "A", "text": passage, "score": 0.9}],
        "clauses": [], "text": [], "graph": []})
    ai.outputs["portfolio_filter"] = PortfolioFilterOutput(is_list_question=False)
    ai.outputs["contract_brain_answer"] = BrainAnswerOutput(
        answer="Capped at fees.", citations=[CitationInput(quote=passage)], confidence="high")

    saved = await ask(world, "Which contracts cap liability, in general terms?")

    assert ai.calls == ["portfolio_filter", "contract_brain_answer"]
    meta = saved.retrieval_metadata
    assert "answer_mode" not in meta
    assert meta["confidence"] == "medium"
    assert "best-matching passages, not every contract" in meta["limitations"]


async def test_a_question_without_list_wording_skips_the_filter_call(world, ai, monkeypatch):
    world.contract("A")
    world.contract("B")
    monkeypatch.setattr(routes, "hybrid_sources", lambda *a, **k: {
        "semantic": [], "clauses": [], "text": [], "graph": []})
    ai.outputs["contract_brain_answer"] = BrainAnswerOutput(answer="Nothing found.")

    saved = await ask(world, "What is our usual liability cap?")

    assert ai.calls == ["contract_brain_answer"]
    assert "best-matching passages" in saved.retrieval_metadata["limitations"]


async def test_a_failed_filter_call_falls_back_to_search_but_a_spending_cap_stops_the_request(world, ai, monkeypatch):
    world.contract("A")
    world.contract("B")
    monkeypatch.setattr(routes, "hybrid_sources", lambda *a, **k: {
        "semantic": [], "clauses": [], "text": [], "graph": []})
    ai.outputs["contract_brain_answer"] = BrainAnswerOutput(answer="Nothing found.")
    ai.outputs["portfolio_filter"] = RuntimeError("model unavailable")

    saved = await ask(world, "Which contracts renew?")
    assert "best-matching passages" in saved.retrieval_metadata["limitations"]

    ai.outputs["portfolio_filter"] = HTTPException(429, "Daily AI budget reached")
    with pytest.raises(HTTPException) as capped:
        await ask(world, "Which contracts renew?")
    assert capped.value.status_code == 429


async def test_contract_scope_never_uses_the_filter(world, ai, monkeypatch):
    """The filter is a portfolio feature: a question about one contract goes to search."""
    contract = world.contract("A")
    monkeypatch.setattr(routes, "hybrid_sources", lambda *a, **k: {
        "semantic": [], "clauses": [], "text": [], "graph": []})
    ai.outputs["contract_brain_answer"] = BrainAnswerOutput(answer="Nothing found.")
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-1"))

    saved = await routes.ask_contract_brain(
        routes.BrainAskRequest(question="How many contracts?", query_scope="contract", contract_id=contract.id),
        request, world.db, world.owner)

    assert ai.calls == ["contract_brain_answer"]
    assert "answer_mode" not in saved.retrieval_metadata


# --- GET /queries/{id}/result ------------------------------------------------------------------------


def save_answer(world, db, result: dict) -> BrainQuery:
    query = BrainQuery(org_id=world.org_id, query_scope="portfolio", question="q", answer=answer_text(result),
                       citations=[], retrieval_metadata=routes._filter_metadata(result),
                       created_by_user_id=world.owner.id, updated_by_user_id=world.owner.id)
    db.add(query)
    db.flush()
    return query


def test_the_result_fills_in_as_the_reading_finishes_and_the_final_list_is_saved(world, db):
    """History must show the final list, not the "still being read" snapshot the answer had
    when it was asked. It is saved only once the reading can no longer change."""
    contracts, review_id, first = read_four(world, db)
    query = save_answer(world, db, first)
    mid = routes.get_brain_query_result(query.id, db=db, current_user=world.owner)
    assert mid["retrieval_metadata"]["matched"] == 0
    # Server-side state (titles of every contract handed to the AI) is kept, never sent.
    assert "candidates" not in mid["retrieval_metadata"] and query.retrieval_metadata["candidates"]
    assert query.retrieval_metadata["reading"]["done"] == 0  # still running: nothing saved

    for title, c in contracts.items():
        settle(db, review_id, c.id, verdict="yes" if title in ("Yes", "No") else "no")
    done = routes.get_brain_query_result(query.id, db=db, current_user=world.owner)

    assert done["retrieval_metadata"]["matched"] == 2 and done["retrieval_metadata"]["complete"] is True
    assert "**2 of 5 contracts match" in done["answer"]
    assert query.retrieval_metadata["matched"] == 2 and query.answer == done["answer"]
    assert query.retrieval_metadata["reading"]["done"] == 5


def test_the_result_is_not_found_for_other_answers_unknown_ids_and_other_users(world, db):
    world.contract("A")
    other = world.user("other")
    answer = routes.BrainQuery(org_id=world.org_id, query_scope="portfolio", question="q", answer="x",
                               retrieval_metadata={"answer_mode": None}, created_by_user_id=world.owner.id)
    db.add(answer)
    filtered = save_answer(world, db, run(world, PortfolioFilter()))

    for query_id, user in ((answer.id, world.owner), ("no-such-id", world.owner), (filtered.id, other)):
        with pytest.raises(HTTPException) as err:
            routes.get_brain_query_result(query_id, db=db, current_user=user)
        assert err.value.status_code == 404


def test_someone_elses_view_of_the_result_is_never_saved_over_the_askers(world, db, monkeypatch):
    """An admin opening the answer sees it for their own access, and does not rewrite the row."""
    world.contract("A")
    query = save_answer(world, db, run(world, PortfolioFilter()))
    admin = world.user("admin")
    monkeypatch.setattr(routes, "is_org_admin", lambda user: True)
    monkeypatch.setattr("app.contracts.access.is_org_admin", lambda user: True)
    before = dict(query.retrieval_metadata)

    seen = routes.get_brain_query_result(query.id, db=db, current_user=admin)

    assert seen["retrieval_metadata"]["total"] == 1
    assert query.retrieval_metadata == before


# --- a saved answer for another viewer ----------------------------------------------------------------


def test_a_saved_filter_answer_is_cut_to_what_the_viewer_can_open():
    """The saved lists name titles, so another viewer gets only contracts they can open, and
    no answer text at all when it drew on one they cannot."""
    meta = {"answer_mode": "database_filter", "contract_ids": ["c-open", "c-walled"],
            "matches": [{"contract_id": "c-open", "title": "Open"}, {"contract_id": "c-walled", "title": "Walled"}],
            "unknown": [{"contract_id": "c-walled", "title": "Walled", "reason": "x"}],
            "candidates": {"c-walled": {"title": "Walled", "why": ["secret"]}},
            "total": 40, "source_count": 40, "complete": True, "confidence": "high"}
    saved = BrainQuery(id="q-1", org_id="org-1", query_scope="portfolio", question="q",
                       answer="1. Open\n2. Walled", citations=[], retrieval_metadata=meta,
                       created_by_user_id="u-asker")

    out = routes._viewer_copy(saved, viewer_id="u-other", accessible_ids={"c-open"})

    assert [m["title"] for m in out["retrieval_metadata"]["matches"]] == ["Open"]
    assert out["retrieval_metadata"]["unknown"] == [] and "candidates" not in out["retrieval_metadata"]
    assert out["retrieval_metadata"]["contract_ids"] == ["c-open"]
    assert "Walled" not in out["answer"] and out["retrieval_metadata"]["withheld_for_viewer"] is True
    # The asker's coverage describes contracts this viewer cannot open.
    assert not {"total", "source_count", "complete", "confidence"} & set(out["retrieval_metadata"])
    assert saved.answer == "1. Open\n2. Walled"  # the stored row is untouched
    everything = routes._viewer_copy(saved, viewer_id="u-other", accessible_ids={"c-open", "c-walled"})
    assert everything["answer"].startswith("1. Open")


# --- the small pure pieces ------------------------------------------------------------------------------


@pytest.mark.parametrize("question, bare, count", [
    ("How many contracts?", True, True),
    ("how many contracts do we have", True, True),
    ("List contracts", True, False),
    ("Show me all agreements.", True, False),
    ("How many NDAs expire in 2027?", False, False),
    ("List all contracts with Acme", False, False),
])
def test_only_a_bare_all_contracts_question_skips_the_model(question, bare, count):
    assert bool(BARE_ALL_RE.match(question.strip())) is bare
    flt = bare_all_filter(question)
    assert (flt is not None) is bare
    if flt:
        assert flt.count_only is count and flt.model_dump(exclude={"count_only"}) == PortfolioFilter().model_dump(
            exclude={"count_only"})


@pytest.mark.parametrize("question, expected", [
    ("Which contracts renew automatically?", True),
    ("How many NDAs expire in 2027", True),
    ("Show me all active contracts", True),
    ("List the MSAs", True),
    ("What contracts are with Acme?", True),
    ("What is our usual liability cap?", False),
    ("Summarise the Acme MSA", False),
])
def test_the_cheap_gate_lets_list_questions_through(question, expected):
    assert looks_like_list_question(question) is expected


def test_search_answers_are_marked_partial_and_never_left_at_high():
    out = mark_search_partial({"confidence": "high", "limitations": "Thin context."}, contract_count=20)
    assert out["confidence"] == "medium"
    assert out["limitations"].startswith("Thin context. Looked at the best-matching passages")
    assert mark_search_partial({"confidence": "low", "limitations": None}, contract_count=20)["confidence"] == "low"
    keep = {"confidence": "high", "limitations": None}
    assert mark_search_partial(keep, contract_count=1) is keep  # one contract: nothing was left out
    assert keep["confidence"] == "high"  # the caller's dict is never edited in place


def test_the_answer_text_is_written_by_code_from_the_result():
    result = {
        "total": 32, "checked": 29, "matched": 2, "conditions": ["type: NDA", "AI reads each contract: Does this contract renew automatically?"],
        "matches": [{"contract_id": "c1", "title": "Acme NDA", "counterparty_name": "Acme", "why": ["type: NDA", "Does this contract renew automatically? Yes — “renews”"]},
                    {"contract_id": "c2", "title": "Beta NDA", "counterparty_name": None, "why": []}],
        "unknown": [{"contract_id": "c3", "title": "X", "reason": "no expiry date recorded"}] * 3,
        "unknown_reasons": {"no expiry date recorded": 3}, "not_checked": ["value (no currency given)"],
        "filter": {"wording": ["Does this contract renew automatically?"]},
        "reading": {"tabular_review_id": "r1", "contracts": 20, "done": 12, "failed": 0, "estimate_usd": 0.84, "started": True},
    }

    text = answer_text(result)

    assert text.startswith("**2 of 32 contracts match:** type: NDA; AI reads each contract:")
    assert "Checked 29 of 32 contracts." in text
    assert "1. **Acme NDA** (Acme)\n   - type: NDA\n   - Does this contract renew automatically? Yes" in text
    assert "2. **Beta NDA**" in text
    assert "Could not check (3): 3 no expiry date recorded." in text
    assert "Not checked: value (no currency given)." in text
    assert "The AI is reading 20 contracts for: “Does this contract renew automatically?” (12 of 20 done, about $0.84)." in text
    done = answer_text({**result, "reading": {**result["reading"], "done": 20}})
    assert "The AI read 20 contracts for:" in done


# --- the model contract -----------------------------------------------------------------------------------


def test_the_filter_skill_is_registered_and_mock_mode_is_not_a_list_question():
    """Mock Claude must keep /ask on its search path, so its canned filter says "not a list
    question"; the skill reads no contract text."""
    spec = skill_registry.get("portfolio_filter")
    assert spec.output_model is PortfolioFilterOutput and spec.include_contract_text is False
    assert spec.feature_flag == skill_registry.get("contract_brain_answer").feature_flag
    assert PortfolioFilterOutput.model_validate(structured_payload_by_tool()[spec.return_tool_name]).is_list_question is False
    assert spec.name != "contract_brain_query_parse"


def test_a_yes_no_cell_keeps_its_typed_verdict_in_what_is_stored(monkeypatch):
    """The filter decides from a cell's `verdict`, so the job handler must keep it in
    raw_ai_output for a row job and for a lone-cell job alike (a verdict dropped there would
    turn every yes into "no yes/no answer")."""
    import asyncio

    from app.jobs import tasks
    from tests.test_tabular_rows_and_fencing import FakeDB, _cell, _columns, _row_job

    async def row_skill(_db, *, job, skill_name, input_payload):
        return TabularRowOutput.model_validate({"answers": [
            {"column_id": "col-a", "answer": "Yes", "verdict": "yes", "citations": [{"quote": "renews"}]},
            {"column_id": "col-b", "verdict": "not_stated", "not_found": True}]})

    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", row_skill)
    a, b = _cell("c-a", "col-a"), _cell("c-b", "col-b")
    asyncio.run(tasks._run_tabular_job(FakeDB([a, b], _columns("col-a", "col-b")), _row_job(["c-a", "c-b"])))
    assert (a.raw_ai_output["verdict"], b.raw_ai_output["verdict"]) == ("yes", "not_stated")
    assert (a.status, b.status) == ("complete", "complete")  # not_stated sets not_found: a definite answer

    async def cell_skill(_db, *, job, skill_name, input_payload):
        return TabularCellOutput(answer="No", verdict="no", citations=[CitationInput(quote="no renewal")])

    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", cell_skill)
    solo = _cell("c-s", "col-a")
    job = _row_job(["c-s"])
    job.job_type, job.metadata_json = "tabular_cell_extraction", {"cell_id": "c-s", "tabular_review_id": "rev-1"}
    asyncio.run(tasks._run_tabular_job(FakeDB([solo], _columns("col-a")), job))
    assert solo.raw_ai_output["verdict"] == "no"
    assert TabularCellOutput().verdict is None  # a column that is not a yes/no question has none
