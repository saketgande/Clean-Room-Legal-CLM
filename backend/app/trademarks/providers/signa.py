"""Signa (https://signa.so) - unified trademark search API across 200+ offices.

Confirm with Signa support whether Indian trademark data is covered before
relying on it as a primary India-coverage source - this client works
regardless of which offices your plan includes, it just passes through
whatever `offices` you configure (or none, to search everything your key
has access to).
"""

import httpx
from pydantic import BaseModel


class SignaResult(BaseModel):
    id: str
    mark_text: str
    relevance_score: float  # 0-100 as returned by Signa
    status_primary: str | None = None
    office_code: str = ""
    filing_date: str | None = None
    owner_name: str | None = None
    nice_classes: list[int] = []


def search(
    query: str,
    *,
    api_key: str | None,
    base_url: str,
    offices: str = "",
    timeout_seconds: float = 5.0,
    limit: int = 10,
) -> list[SignaResult]:
    if not api_key:
        raise ValueError("SIGNA_API_KEY is not configured")

    params = {"q": query}
    if offices.strip():
        params["offices"] = offices.strip()

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            params=params,
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("data", [])[:limit]:
        status = item.get("status") or {}
        classifications = item.get("classifications") or []
        results.append(
            SignaResult(
                id=item.get("id", ""),
                mark_text=item.get("mark_text", ""),
                relevance_score=float(item.get("relevance_score", 0)),
                status_primary=status.get("primary"),
                office_code=item.get("office_code", ""),
                filing_date=item.get("filing_date"),
                owner_name=item.get("owner_name"),
                nice_classes=[
                    c.get("nice_class") for c in classifications if c.get("nice_class") is not None
                ],
            )
        )
    return results
