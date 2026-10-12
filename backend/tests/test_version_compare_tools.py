"""The chat can list a contract's versions and compare two of them.

Before this the assistant had no tool that reads anything but the current version, so asked to
"compare v7 and v6" it said so and then described a Versions tab and a diff view that the app does not
have. The two tools below reuse the diff the web page already has (ContractService.compute_version_diff);
what is new is looking a version up by the number people say ("v6"), saying so plainly when that is not
possible, and handing a long diff over in windows.

Runs against Postgres inside one rolled-back transaction; no AI is called.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.ai import tool_runtime as tr
from app.ai.tool_registry import (
    CompareContractVersionsInput,
    ListContractVersionsInput,
    tool_registry,
)
from app.auth.models import User
from app.contract_files.models import ContractTextSnapshot
from app.contracts.service import ContractService
from app.core.enums import AssistantToolCategory
from tests import test_nda_fast_lane
from tests.test_nda_fast_lane import _nda, add_version

db = test_nda_fast_lane.db  # the same rolled-back-transaction fixture

LONG = "\n".join(f"{n}. Clause number {n} stays exactly as it was." for n in range(1, 41))


class Versions:
    """A contract whose versions we build one by one."""

    def __init__(self, db):
        self.db = db
        self.contract = _nda(db)
        self.user = db.get(User, self.contract.owner_user_id)
        self.by_number = {}

    def add(self, number, text, *, current=False, source=None, text_missing=False, deleted=False):
        version = add_version(self.db, self.contract, number)
        if source:
            version.source = source
        if not text_missing:
            snap = ContractTextSnapshot(
                org_id=self.contract.org_id, contract_id=self.contract.id, contract_version_id=version.id,
                extraction_method="text", text=text,
            )
            self.db.add(snap)
            self.db.flush()
            version.text_snapshot_id = snap.id
        if current:
            version.is_authoritative = True
            self.contract.current_authoritative_version_id = version.id
        if deleted:
            from app.core.database import utcnow

            version.deleted_at = utcnow()
        self.db.flush()
        self.by_number[number] = version
        return version

    def run(self, tool, **fields):
        payload = (
            ListContractVersionsInput if tool == "list_contract_versions" else CompareContractVersionsInput
        )(contract_id=self.contract.id, **fields)
        return asyncio.run(
            tr.tool_runtime._execute_validated(
                self.db, tool_name=tool, payload=payload, user=self.user, session_id="s-1"
            )
        )

    def compare(self, **fields):
        return self.run("compare_contract_versions", **fields)


@pytest.fixture
def v(db):
    w = Versions(db)
    w.add(5, "Term: one year.\nLiability: capped.\nLaw: England.", source="upload")
    w.add(6, "Term: one year.\nLiability: capped.\nLaw: England.\nNew: audit rights.", source="redline")
    w.add(7, "Term: two years.\nLiability: capped.\nLaw: England.\nNew: audit rights.", current=True, source="upload")
    return w


# --- the tools exist and are safe to call -----------------------------------

@pytest.mark.parametrize("name", ["list_contract_versions", "compare_contract_versions"])
def test_the_tools_are_read_only_and_need_the_file_read_permission(name):
    spec = tool_registry.get(name)

    assert spec.category == AssistantToolCategory.READ_ONLY
    assert spec.required_permission == "contract_file:read"  # the permission the version text page needs
    assert not spec.requires_confirmation
    assert "*" in spec.exposed_session_types


# --- listing -----------------------------------------------------------------

def test_listing_shows_every_version_oldest_first_and_which_one_is_in_use(v):
    out = v.run("list_contract_versions")

    assert [x["version_number"] for x in out["versions"]] == [5, 6, 7]
    assert [x["state"] for x in out["versions"]] == ["earlier", "earlier", "current"]
    assert [x["is_current"] for x in out["versions"]] == [False, False, True]
    assert out["versions"][1]["source"] == "redline"
    assert all(x["has_text"] for x in out["versions"])
    assert out["current_version_number"] == 7


def test_a_version_newer_than_the_current_one_is_called_not_in_use(v):
    v.add(8, "Term: three years.", source="redline")  # a proposal nobody applied

    out = v.run("list_contract_versions")

    assert out["versions"][-1]["version_number"] == 8
    assert out["versions"][-1]["state"] == "not in use"


def test_a_deleted_version_is_not_listed(v):
    v.add(9, "gone", deleted=True)

    assert [x["version_number"] for x in v.run("list_contract_versions")["versions"]] == [5, 6, 7]


def test_a_version_whose_text_was_never_read_says_so(v):
    v.add(8, "", text_missing=True)

    assert v.run("list_contract_versions")["versions"][-1]["has_text"] is False


# --- comparing ---------------------------------------------------------------

def test_comparing_two_versions_by_number_returns_what_changed(v):
    out = v.compare(base_version_number=6, target_version_number=7)

    assert out["found"] is True
    assert (out["base"]["version_number"], out["target"]["version_number"]) == (6, 7)
    assert (out["lines_added"], out["lines_removed"]) == (1, 1)
    assert "- Term: one year." in out["diff"]
    assert "+ Term: two years." in out["diff"]
    assert out["identical"] is False and out["has_more"] is False


def test_without_numbers_the_previous_version_is_compared_with_the_current_one(v):
    out = v.compare()

    assert (out["base"]["version_number"], out["target"]["version_number"]) == (6, 7)


def test_with_only_the_newer_number_the_one_before_it_is_the_base(v):
    out = v.compare(target_version_number=6)

    assert (out["base"]["version_number"], out["target"]["version_number"]) == (5, 6)
    assert out["lines_added"] == 1 and out["lines_removed"] == 0


def test_unchanged_stretches_are_collapsed_but_the_changes_keep_their_neighbours(db):
    w = Versions(db)
    w.add(1, LONG)
    w.add(2, LONG.replace("Clause number 20 stays exactly as it was.", "Clause number 20 now says something else."), current=True)

    out = w.compare(base_version_number=1, target_version_number=2, context_lines=2)

    assert "- 20. Clause number 20 stays exactly as it was." in out["diff"]
    assert "+ 20. Clause number 20 now says something else." in out["diff"]
    assert "  19. Clause number 19 stays exactly as it was." in out["diff"]   # a neighbour is kept
    assert "  1. Clause number 1 stays exactly as it was." not in out["diff"]  # far away: not sent
    assert any(line.startswith("… ") and "unchanged" in line for line in out["diff"])
    assert len(out["diff"]) < 15


def test_identical_text_is_reported_as_identical(db):
    w = Versions(db)
    w.add(1, LONG)
    w.add(2, LONG, current=True)

    out = w.compare(base_version_number=1, target_version_number=2)

    assert out["identical"] is True and out["diff"] == [] and out["lines_added"] == out["lines_removed"] == 0


def test_a_long_diff_comes_back_in_windows_that_add_up_to_the_whole(db, monkeypatch):
    monkeypatch.setattr("app.contracts.version_compare.WINDOW_CHARS", 400)
    w = Versions(db)
    w.add(1, "\n".join(f"Line {n} before" for n in range(60)))
    w.add(2, "\n".join(f"Line {n} after" for n in range(60)), current=True)

    seen, offset, rounds = [], 0, 0
    while offset is not None:
        out = w.compare(base_version_number=1, target_version_number=2, diff_offset=offset)
        assert out["diff_offset"] == offset and out["diff"]
        seen.extend(out["diff"])
        offset = out["next_offset"]
        assert out["has_more"] == (offset is not None)
        rounds += 1
        assert rounds < 50

    assert rounds > 2
    assert seen.count("- Line 0 before") == 1 and seen.count("+ Line 59 after") == 1
    assert len([x for x in seen if x.startswith("- ")]) == 60 == len([x for x in seen if x.startswith("+ ")])


# --- when it cannot be done, say so (no exception, no guessing) ---------------

def test_an_unknown_version_number_lists_what_exists(v):
    out = v.compare(base_version_number=2, target_version_number=7)

    assert out["found"] is False and out["reason"] == "no_such_version"
    assert "2" in out["message"]
    assert [x["version_number"] for x in out["versions"]] == [5, 6, 7]


def test_a_number_two_files_share_is_not_guessed(v):
    twin = v.add(6, "Term: one year.\nLiability: unlimited.", source="upload")  # a second file's v6

    out = v.compare(base_version_number=6, target_version_number=7)

    assert out["found"] is False and out["reason"] == "ambiguous_version"
    assert {c["version_id"] for c in out["candidates"]} == {v.by_number[6].id, twin.id} or len(out["candidates"]) == 2
    # naming the exact one works
    ok = v.compare(base_version_id=twin.id, target_version_number=7)
    assert ok["found"] is True and ok["base"]["version_id"] == twin.id


def test_a_version_without_text_cannot_be_compared(v):
    v.add(8, "", text_missing=True)

    out = v.compare(base_version_number=7, target_version_number=8)

    assert out["found"] is False and out["reason"] == "version_has_no_text"
    assert "8" in out["message"]


def test_comparing_a_version_with_itself_is_refused_politely(v):
    out = v.compare(base_version_number=7, target_version_number=7)

    assert out["found"] is False and out["reason"] == "same_version"


def test_the_first_version_has_nothing_before_it(db):
    w = Versions(db)
    w.add(1, "Only version.", current=True)

    out = w.compare()

    assert out["found"] is False and out["reason"] == "no_earlier_version"


def test_a_version_of_another_contract_is_not_reachable_by_id(v, db):
    other = Versions(db)
    foreign = other.add(1, "Somebody else's contract.", current=True)

    out = v.compare(base_version_id=foreign.id, target_version_number=7)

    assert out["found"] is False and out["reason"] == "no_such_version"
    assert "Somebody else" not in str(out)


# --- the contract wall holds ---------------------------------------------------

@pytest.mark.parametrize("tool", ["list_contract_versions", "compare_contract_versions"])
def test_a_contract_the_user_is_walled_from_cannot_be_read(v, monkeypatch, tool):
    def walled(self, *, contract_id, user):
        raise HTTPException(404, "Contract not found")

    monkeypatch.setattr(ContractService, "get_contract_for_user", walled)

    with pytest.raises(HTTPException) as exc:
        v.run(tool)

    assert exc.value.status_code == 404


# --- the diff itself (shared with the web page) ----------------------------------

def test_the_diff_keeps_its_old_shape_when_no_context_is_asked_for(v):
    out = ContractService(v.db).compute_version_diff(
        contract=v.contract, base_version_id=v.by_number[6].id, target_version_id=v.by_number[7].id
    )

    assert {line["type"] for line in out["lines"]} == {"context", "add", "remove"}
    assert len(out["lines"]) == 5  # nothing was left out


def test_a_diff_of_a_very_long_contract_still_counts_every_change(db, monkeypatch):
    monkeypatch.setattr("app.contracts.service._DIFF_MAX_LINES", 50)
    w = Versions(db)
    w.add(1, "\n".join(f"Line {n}" for n in range(200)))
    w.add(2, "\n".join(f"Line {n}" if n % 10 else f"Changed {n}" for n in range(200)), current=True)

    out = ContractService(db).compute_version_diff(
        contract=w.contract, base_version_id=w.by_number[1].id, target_version_id=w.by_number[2].id
    )

    assert out["truncated"] is True and len(out["lines"]) == 50
    assert (out["added"], out["removed"]) == (20, 20)  # the totals are not cut off with the lines


def test_with_context_a_late_change_in_a_long_contract_is_not_cut_off(db, monkeypatch):
    monkeypatch.setattr("app.contracts.service._DIFF_MAX_LINES", 50)
    w = Versions(db)
    w.add(1, "\n".join(f"Line {n}" for n in range(500)))
    w.add(2, "\n".join(f"Line {n}" if n != 480 else "Changed 480" for n in range(500)), current=True)

    out = ContractService(db).compute_version_diff(
        contract=w.contract, base_version_id=w.by_number[1].id, target_version_id=w.by_number[2].id, context=2
    )

    assert out["truncated"] is False
    assert {"type": "add", "text": "Changed 480"} in out["lines"]
    assert any(line["type"] == "gap" for line in out["lines"])
    assert len(out["lines"]) < 12


def test_the_web_route_accepts_the_same_context_option(v):
    from app.contracts import routes as contract_routes

    out = contract_routes.get_version_diff(
        v.contract.id, v.by_number[6].id, v.by_number[7].id, context=0,
        service=ContractService(v.db), current_user=v.user,
    )

    # the change, then one note that the rest (three lines) is unchanged
    assert [line["type"] for line in out["lines"]] == ["remove", "add", "gap"]
    assert out["lines"][-1]["text"] == "3 unchanged lines"
