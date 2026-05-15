import re
from datetime import date

from app.ai.schemas import ContractMetadataOutput


DATE_PATTERNS = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
]


def fallback_metadata_from_text(text: str, *, filename: str | None = None) -> ContractMetadataOutput:
    title = None
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) >= 5:
            title = stripped[:200]
            break
    effective_date = _first_date(text)
    return ContractMetadataOutput(
        title=title or filename,
        effective_date=effective_date,
        confidence="low",
        citations=[],
        notes="Fallback metadata was produced without Claude.",
    )


def _first_date(text: str) -> date | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            if len(match.groups()) == 1:
                return date.fromisoformat(match.group(1))
            month, day, year = match.groups()
            return date(int(year), int(month), int(day))
        except ValueError:
            continue
    return None
