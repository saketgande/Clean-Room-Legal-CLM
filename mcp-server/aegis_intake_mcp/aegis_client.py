"""The only door to the platform (FR-2, FR-13, FR-54, FR-57).

* One shared `httpx.AsyncClient` pointed at AEGIS_API_BASE_URL. `trust_env=False` means no
  proxy environment variable can reroute traffic; `follow_redirects=False` means a redirect
  can never carry the caller's Bearer key to another host.
* Only the nine allow-listed (method, path-template) pairs can be called. Anything else,
  including an absolute URL, raises `ClientPolicyError` before any network I/O.
* The Authorization header is an argument of each call. It is never put in the client's
  default headers, an attribute, a cache or a log line.
* Retries: reads and create retry once with the identical body after a short backoff on
  connect error / timeout / 502 / 503 / 504. `add_document` never retries (not idempotent).
  No other status is retried (4xx and 500 come back as responses).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

import httpx

from .config import Config

# (method, path template) -> may be retried. This dict IS the allow-list.
ALLOWED_CALLS: dict[tuple[str, str], bool] = {
    ("GET", "/intake/forms"): True,
    ("GET", "/legal-entities"): True,
    ("GET", "/counterparties"): True,
    ("POST", "/intake/requests"): True,
    ("GET", "/intake/requests/mine"): True,
    ("GET", "/intake/requests/{id}"): True,
    ("GET", "/intake/requests/{id}/sla"): True,
    ("GET", "/intake/requests/{id}/documents"): True,
    ("POST", "/intake/requests/{id}/documents"): False,
}

RETRYABLE_STATUSES = frozenset({502, 503, 504})
_PATH_PARAM = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_HEADER_FORBIDDEN = re.compile(r"[\r\n\x00]")


class ClientPolicyError(Exception):
    """A call outside the allow-list or with unsafe parts. A bug in a tool, never user input
    that should reach the platform."""


class UpstreamUnavailable(Exception):
    """The platform could not be reached or kept answering 502/503/504, after any retries.

    `reason` is "connect", "timeout" or "status"; `status_code` is set for "status".
    """

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        detail = f"{reason} {status_code}" if status_code else reason
        super().__init__(f"upstream unavailable ({detail})")
        self.reason = reason
        self.status_code = status_code


class AegisClient:
    def __init__(
        self,
        config: Config,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._config = config
        self._backoff = retry_backoff_seconds
        # No headers= here, on purpose: the caller's key is per call (FR-11).
        self._http = httpx.AsyncClient(
            base_url=config.api_base_url,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(
                config.read_timeout_seconds, connect=config.connect_timeout_seconds
            ),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _timeout(self, method: str) -> httpx.Timeout:
        c = self._config
        wait = c.write_timeout_seconds if method == "POST" else c.read_timeout_seconds
        return httpx.Timeout(wait, connect=c.connect_timeout_seconds)

    @staticmethod
    def _build_path(path_template: str, path_params: dict[str, str] | None) -> str:
        params = path_params or {}
        wanted = set(re.findall(r"{(\w+)}", path_template))
        if wanted != set(params):
            raise ClientPolicyError(f"path parameters {sorted(params)} do not fit {path_template}")
        for name, value in params.items():
            if not isinstance(value, str) or not _PATH_PARAM.match(value):
                raise ClientPolicyError(f"path parameter {name!r} has an unsafe value")
        return path_template.format(**{k: quote(v, safe="") for k, v in params.items()})

    async def request(
        self,
        method: str,
        path_template: str,
        *,
        auth_header: str,
        path_params: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Call one allow-listed endpoint with the caller's own `Authorization` value.

        Returns the response for every status except a retry-exhausted 502/503/504, which
        raises `UpstreamUnavailable` (as do connect errors and timeouts).
        """
        method = method.upper()
        key = (method, path_template)
        if key not in ALLOWED_CALLS:
            raise ClientPolicyError(f"{method} {path_template} is not an allowed Aegis call")
        if not isinstance(auth_header, str) or not auth_header or _HEADER_FORBIDDEN.search(
            auth_header
        ):
            raise ClientPolicyError("auth_header must be a single-line, non-empty string")
        path = self._build_path(path_template, path_params)

        attempts = max(1, self._config.attempts) if ALLOWED_CALLS[key] else 1
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                response = await self._http.request(
                    method,
                    path,
                    headers={"Authorization": auth_header},
                    json=json,
                    params=params,
                    timeout=self._timeout(method),
                )
            except httpx.TimeoutException:
                failure = UpstreamUnavailable("timeout")
            except httpx.TransportError:
                failure = UpstreamUnavailable("connect")
            else:
                if response.status_code not in RETRYABLE_STATUSES:
                    return response
                failure = UpstreamUnavailable("status", response.status_code)
            if last:
                raise failure
            await asyncio.sleep(self._backoff)
        raise AssertionError("unreachable")  # pragma: no cover
