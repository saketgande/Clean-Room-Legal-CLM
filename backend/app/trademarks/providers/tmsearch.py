"""TMSearch.ai - https://tmsearch.ai/trademark/api/manual.html (v3.03)

Authentication is a POST form field (`api_key`), NOT an Authorization
header - their docs explicitly say to use POST to keep the key out of
URLs/logs.
"""

import json

import httpx
from pydantic import BaseModel


class TmSearchResult(BaseModel):
    mid: str
    verbal: str
    accuracy: int
    status: str | None = None
    submition: str | None = None
    app: str | None = None
    reg: str | None = None
    applied_date: str | None = None
    protection: list[str] = []
    img: str | None = None

    @property
    def image_url(self) -> str | None:
        # Per their manual: https://img.tmsearch.ai/img/{size}/ + img value.
        # 210 is their fastest/smallest size, appropriate for a result list.
        if not self.img:
            return None
        return f"https://img.tmsearch.ai/img/210/{self.img}"


def search(
    keyword: str,
    *,
    api_key: str | None,
    base_url: str,
    timeout_seconds: float = 10.0,
    limit: int = 10,
) -> list[TmSearchResult]:
    if not api_key:
        raise ValueError("TMSEARCH_API_KEY is not configured")

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            base_url,
            data={"keyword": keyword, "api_key": api_key},
        )
        response.raise_for_status()
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            # TMSearch has been observed returning HTTP 200 with a
            # truncated/malformed body when the API key is invalid, inactive,
            # or the account has an issue on their end.
            raise ValueError(
                "TMSearch returned an invalid/unparseable response "
                "(often means TMSEARCH_API_KEY is invalid or inactive "
                f"- check the TMSearch dashboard). Raw response: {response.text!r}"
            ) from exc

    results = []
    for item in data.get("result", [])[:limit]:
        date_info = item.get("date") or {}
        results.append(
            TmSearchResult(
                mid=str(item.get("mid", "")),
                verbal=item.get("verbal", ""),
                accuracy=int(item.get("accuracy", 0)),
                status=item.get("status"),
                submition=item.get("submition"),
                app=item.get("app"),
                reg=item.get("reg"),
                applied_date=date_info.get("applied"),
                protection=item.get("protection", []),
                img=item.get("img"),
            )
        )
    return results
