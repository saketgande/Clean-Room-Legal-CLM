"""ts_headline must not injure the text it excerpts.

Postgres rejects a bare empty selector in the headline options string: written
as ``StartSel=, StopSel=,`` it reads the separating comma as the selector and
wraps every matched word in commas. The damage is silent — the excerpt still
looks plausible — but it corrupts the text shown as a citation, it is fed to
the model as context, and it stops the excerpt from being a verbatim substring
of the snapshot, which is what the text lens uses to recover char offsets.
"""

import pytest
from sqlalchemy import Text, func, select

from app.core.database import SessionLocal
from app.search.fts import HEADLINE_OPTS, or_terms

SENTENCE = "THEORY OF LIABILITY AND WHETHER OR NOT SUCH PARTY HAS BEEN ADVISED OF THE POSSIBILITY"


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_headline_fragments_stay_verbatim_substrings(db):
    headline = db.scalar(
        select(
            func.ts_headline(
                "english",
                SENTENCE,
                func.websearch_to_tsquery("english", "liability"),
                HEADLINE_OPTS,
            )
        )
    )
    for fragment in headline.split(" ... "):
        assert fragment.strip() in SENTENCE, f"headline mangled the source text: {headline!r}"


def test_a_question_form_query_does_not_and_itself_to_nothing(db):
    """websearch_to_tsquery ANDs its terms, which zeroes the keyword lenses.

    /ask always passes a natural-language question, so one word that appears
    nowhere in the corpus ("MSAs", a typo) drops the clause and raw-text lenses
    to zero results and leaves the answer grounded on the vector lens alone.
    or_terms() is the fallback that keeps them alive; if it ever stops
    producing an OR query, that regression is silent.
    """
    question = "What is the limitation of liability cap in our MSAs?"
    strict, loose = db.execute(
        select(
            func.cast(func.websearch_to_tsquery("english", question), Text),
            func.cast(func.websearch_to_tsquery("english", or_terms(question)), Text),
        )
    ).one()
    assert "&" in strict and "|" not in strict
    assert "|" in loose and "&" not in loose
