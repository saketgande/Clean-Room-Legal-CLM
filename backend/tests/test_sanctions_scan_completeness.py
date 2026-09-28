"""A sanctions screen must not report "clear" without having finished looking.

The scan narrowed with an ILIKE on the query's longest token and took the first
200 rows. "Company" matches 2,674 rows of the real SDN list and "Limited"
2,322 — so genuinely designated entities could not be found by a search for
their own exact name, and the verdict came back `clear`:

    JOINT STOCK COMPANY PLASMA   RUSSIA-EO14024   invisible
    CHERY STAR CO., LIMITED      SDGT / IFSR      invisible
    HOZDRA GROUP LIMITED         IRAN-EO13902     invisible

Those are real rows from the live Treasury list, found by searching it for
entries the cap hides. `clear` is the one answer this function must never give
when it has not actually looked.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.core.database import utcnow
from app.intake import screening


class _FakeDB:
    """Stands in for the list so these tests pin behaviour, not OFAC's data."""

    def __init__(self, names, *, refreshed=None, row_factory=None):
        self._names = names
        self._refreshed = refreshed or utcnow()
        self._row_factory = row_factory

    # `newest refreshed_at` freshness probe
    def query(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def scalar(self):
        return self._refreshed

    # the candidate scan
    def execute(self, statement):
        limit = statement._limit if hasattr(statement, "_limit") else None
        rows = [
            SimpleNamespace(name=n, source="OFAC_SDN", source_ref=str(i), programs="TEST")
            for i, n in enumerate(self._names)
        ]
        if limit:
            rows = rows[:limit]
        return SimpleNamespace(all=lambda: rows)


def _screen(names, name, **kw):
    return screening.screen_sanctions(_FakeDB(names, **kw), "org-1", name)


# --- the cap ----------------------------------------------------------------


def test_an_entity_past_the_old_cap_is_still_found():
    """The defect this file exists for. The designated entity sits at position
    1,000 of a list whose generic tokens would have filled the first 200."""
    filler = [f"JOINT STOCK COMPANY FILLER {i}" for i in range(1000)]
    result = _screen([*filler, "JOINT STOCK COMPANY PLASMA"], "JOINT STOCK COMPANY PLASMA")

    assert result["status"] == "hit"
    assert result["matches"][0]["name"] == "JOINT STOCK COMPANY PLASMA"


def test_the_exact_name_ranks_first_among_many_candidates():
    """Removing the cap alone would not have helped: the display kept only ten
    matches, unranked, so the designated entity could still have been absent
    from what a reviewer saw."""
    noise = [f"PLASMA HOLDINGS {i} LIMITED" for i in range(50)]
    result = _screen([*noise, "JOINT STOCK COMPANY PLASMA"], "JOINT STOCK COMPANY PLASMA")

    assert result["matches"][0]["name"] == "JOINT STOCK COMPANY PLASMA"


def test_a_list_too_large_to_scan_is_unavailable_not_clear(monkeypatch):
    """The guard that stops this bug returning when the corpus grows. A sampled
    scan is an unfinished scan, and must fail closed exactly as a stale list
    does."""
    monkeypatch.setattr(screening, "_MAX_SCAN_ROWS", 10)
    result = _screen([f"ENTITY {i}" for i in range(50)], "Entity 3")

    assert result["status"] == "unavailable"
    assert "incomplete" in result["note"]


def test_a_truncated_display_says_how_many_there_were():
    """Guards a reviewer reading ten matches as "ten matches" when there were
    hundreds."""
    names = [f"PLASMA VENTURE {i}" for i in range(40)]
    result = _screen(names, "Plasma Venture Partners")

    assert len(result["matches"]) == screening._MAX_REPORTED_MATCHES
    assert result["match_count"] == 40
    assert "40 candidate matches" in result["note"]


# --- precision --------------------------------------------------------------


def test_boilerplate_alone_is_not_a_match():
    """Two names sharing only "company" and "limited" cleared the two-token
    bar while having nothing to do with each other — which is what produced
    795 candidates for a single ordinary company name."""
    result = _screen(["JOINT STOCK COMPANY ROSTEC"], "Widget Company Limited")
    assert result["status"] == "clear"


def test_a_distinctive_token_pair_still_matches():
    """The positive control: stripping boilerplate must not strip identity."""
    result = _screen(["ACME PLASMA TRADING LIMITED"], "Acme Plasma Ltd")
    assert result["status"] == "hit"


def test_an_entity_suffix_difference_does_not_prevent_a_match():
    """"Umbrella Corp" and "Umbrella Corporation" are the same counterparty."""
    result = _screen(["UMBRELLA CORPORATION"], "Umbrella Corp")
    assert result["status"] == "hit"


# --- the wildcard that went with the ILIKE ----------------------------------


def test_sql_wildcards_in_a_counterparty_name_are_inert():
    """The scan used `ilike(f"%{anchor}%")` on an unescaped token, so `%` and
    `_` in a counterparty name were wildcards. Scanning every row removes the
    pattern entirely."""
    result = _screen(["ACME PLASMA LIMITED"], "%_% %_%")
    assert result["status"] in {"clear", "unavailable"}
    assert not result["matches"]


# --- the guards that were already right -------------------------------------


def test_a_stale_list_is_still_unavailable():
    result = _screen(["ACME PLASMA LIMITED"], "Acme Plasma",
                     refreshed=utcnow() - timedelta(days=45))
    assert result["status"] == "unavailable"


def test_an_embargoed_jurisdiction_still_hits_regardless_of_the_list():
    """A named embargoed jurisdiction is a hit even with no list at all."""
    result = _screen([], "Iran Plasma Trading", refreshed=utcnow() - timedelta(days=999))
    assert result["status"] == "hit"


@pytest.mark.parametrize("innocent", ["Miranda Holdings Ltd", "Cubana Foods SA",
                                      "Syriac Press Limited"])
def test_a_jurisdiction_name_inside_a_word_is_not_an_embargo_hit(innocent):
    """Found while testing the scan: a substring test blocked "Miranda" on
    `iran`, "Cubana" on `cuba` and "Syriac" on `syria`. A false embargo hit is
    a hard stop on a legitimate counterparty."""
    assert _screen(["ACME PLASMA LIMITED"], innocent)["status"] != "hit"


def test_a_name_with_nothing_screenable_is_unavailable():
    assert _screen(["ACME"], "Ltd")["status"] == "unavailable"


# --- against the real list --------------------------------------------------


@pytest.mark.parametrize("designated", [
    "JOINT STOCK COMPANY PLASMA",
    "CHERY STAR CO., LIMITED",
    "HOZDRA GROUP LIMITED",
])
def test_real_designated_entities_are_found_by_their_own_name(monkeypatch, designated):
    """End to end against whatever the deployment's list actually holds. Skips
    rather than fails when the list has not been refreshed — the point is the
    scan, not OFAC's contents."""
    from sqlalchemy import select

    import app.models  # noqa: F401  (register every mapper)
    from app.core.database import SessionLocal
    from app.intake.models import SanctionsListEntry

    db = SessionLocal()
    try:
        org = db.scalar(select(SanctionsListEntry.org_id))
        if org is None:
            pytest.skip("no sanctions list loaded in this database")
        present = db.scalar(
            select(SanctionsListEntry.id).where(SanctionsListEntry.name == designated)
        )
        if present is None:
            pytest.skip(f"{designated} not in this deployment's list")
        # Only the freshness gate is relaxed; it is a separate, correct guard.
        monkeypatch.setattr(screening, "STALE_AFTER", timedelta(days=36500))

        result = screening.screen_sanctions(db, org, designated)
        assert result["status"] == "hit"
        assert result["matches"][0]["name"] == designated
    finally:
        db.close()
