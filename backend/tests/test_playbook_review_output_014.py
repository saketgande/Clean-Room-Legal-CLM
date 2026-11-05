import json

import pytest
from pydantic import ValidationError

from app.ai.schemas import PlaybookReviewOutput

CITE = [{"quote": "Payment is due in 30 days."}]
DEV = [{"clause_type": "payment_terms", "issue": "too long", "citations": CITE}]


def test_stringified_citations_list_parses():
    """Real review sent citations as JSON text; the whole clean review was recorded ai_unavailable."""
    out = PlaybookReviewOutput.model_validate({"deviations": [], "citations": json.dumps(CITE)})
    assert out.citations[0].quote == CITE[0]["quote"]


def test_stringified_deviations_and_their_citations_parse():
    """Same failure on the deviations list and on a deviation's own citations."""
    d = [{**DEV[0], "citations": json.dumps(CITE)}]
    out = PlaybookReviewOutput.model_validate({"deviations": json.dumps(d)})
    assert out.deviations[0].citations[0].quote == CITE[0]["quote"]


@pytest.mark.parametrize("bad", ["not json at all", '{"quote": "x"}', '"text"'])
def test_text_that_is_not_a_json_list_still_fails(bad):
    """The tolerance must not hide a genuinely malformed payload."""
    with pytest.raises(ValidationError):
        PlaybookReviewOutput.model_validate({"citations": bad})
    with pytest.raises(ValidationError):
        PlaybookReviewOutput.model_validate({"deviations": bad})
