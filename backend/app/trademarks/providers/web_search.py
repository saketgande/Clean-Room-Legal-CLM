"""Serper.dev - simple REST wrapper around Google-fidelity web search results.

Used as the "context only" fourth similarity-search source (not a
trademark-register match, just public web context on the proposed name).
"""

import httpx
from pydantic import BaseModel

SERPER_ENDPOINT = "https://google.serper.dev/search"


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str


def search(
    query: str,
    *,
    api_key: str | None,
    timeout_seconds: float = 5.0,
    num: int = 5,
) -> list[WebSearchResult]:
    if not api_key:
        raise ValueError("SEARCH_PROVIDER_API_KEY is not configured")

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            SERPER_ENDPOINT,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
        )
        response.raise_for_status()
        data = response.json()

    organic = data.get("organic", [])[:num]
    return [
        WebSearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in organic
    ]
