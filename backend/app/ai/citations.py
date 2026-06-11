import re
from difflib import SequenceMatcher

from app.ai.schemas import CitationInput, CitationValidationResult

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - rapidfuzz is optional in bare dev shells
    fuzz = None


def normalize_for_citation(value: str) -> str:
    value = value.lower()
    value = value.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    value = re.sub(r"-\s*\n\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^\w\s$%.,:/-]", "", value)
    return value.strip()


def citation_similarity(needle: str, haystack: str) -> float:
    normalized_needle = normalize_for_citation(needle)
    normalized_haystack = normalize_for_citation(haystack)
    if not normalized_needle:
        return 0.0
    if normalized_needle in normalized_haystack:
        return 100.0
    if fuzz is not None:
        return float(fuzz.partial_ratio(normalized_needle, normalized_haystack))
    return SequenceMatcher(None, normalized_needle, normalized_haystack).ratio() * 100


def validate_citation(citation: CitationInput, source_text: str, *, is_ocr: bool = False) -> CitationValidationResult:
    score = citation_similarity(citation.quote, source_text)
    word_count = len(citation.quote.split())
    threshold = 82.0 if is_ocr else 90.0
    if word_count < 12:
        threshold = max(threshold, 92.0)
    status = "valid" if score >= threshold else "invalid"
    return CitationValidationResult(
        citation=citation,
        validation_status=status,
        similarity_score=score,
        normalized_quote=normalize_for_citation(citation.quote),
        message=None if status == "valid" else f"Citation similarity {score:.1f} below threshold {threshold:.1f}",
    )


def validate_citations(
    citations: list[CitationInput],
    source_text: str,
    *,
    is_ocr: bool = False,
) -> list[CitationValidationResult]:
    return [validate_citation(citation, source_text, is_ocr=is_ocr) for citation in citations]
