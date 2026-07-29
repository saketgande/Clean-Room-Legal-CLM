"""Shared resiliency primitives for the outbound integration clients.

Two concerns live here so the per-provider modules (``claude.py``,
``docusign.py``, ``resend.py``) don't each re-implement them:

1. A tenacity-based async retry decorator that retries ONLY transient network
   faults (connect/read timeouts, dropped connections) plus 429/5xx — never a
   4xx, which is a client error that won't fix itself on retry.
2. A circuit breaker (``pybreaker``) that trips after repeated failures so a
   hard-down provider stops eating request latency. ``pybreaker`` is an
   optional dependency: when it isn't installed the breaker degrades to a
   transparent pass-through, so importing this module never fails.

Both are intentionally additive — wrapping a call changes its failure mode but
never its success-path return value or signature.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable, TypeVar

import anthropic
import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings


logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_transient_error(exc: BaseException) -> bool:
    """True for faults worth retrying: transport errors, timeouts, 429, 5xx.

    A 4xx other than 429 means the request itself is wrong (bad auth, bad
    payload); retrying only wastes the provider's budget, so we return False.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    # The anthropic SDK wraps httpx errors in its own exception hierarchy
    # instead of raising httpx's directly, so the checks above never match a
    # Claude-side failure — mirror them here for anthropic.APIStatusError /
    # APIConnectionError (which also covers APITimeoutError, a subclass).
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or 500 <= exc.status_code < 600
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    # httpx.TimeoutException is a subclass of httpx.TransportError, but list it
    # explicitly for clarity. ConnectionError covers non-httpx callers.
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException, ConnectionError))


def with_async_retry(
    func: Callable[..., Awaitable[T]] | None = None,
    *,
    max_attempts: int | None = None,
) -> Callable[..., Awaitable[T]] | Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorate an async function with transient-only exponential-backoff retry.

    Backoff knobs reuse the existing Claude settings so behavior is uniform and
    centrally tunable. ``max_attempts`` defaults to ``settings.claude_max_retries``
    (read lazily at call time so test overrides take effect).
    """

    def _decorate(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> T:
            attempts = max(1, max_attempts if max_attempts is not None else settings.claude_max_retries)
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(attempts),
                    wait=wait_exponential(
                        multiplier=settings.claude_retry_initial_backoff_seconds,
                        max=settings.claude_retry_max_backoff_seconds,
                    ),
                    retry=retry_if_exception(is_transient_error),
                    reraise=True,
                ):
                    with attempt:
                        return await fn(*args, **kwargs)
            except RetryError as exc:  # pragma: no cover - reraise=True covers normal path
                raise exc.last_attempt.exception() from exc
            # Unreachable; AsyncRetrying always yields at least one attempt.
            raise RuntimeError("async retry loop terminated without a result")

        return _wrapper

    # Support both ``@with_async_retry`` and ``@with_async_retry(max_attempts=5)``.
    if func is not None:
        return _decorate(func)
    return _decorate


class ProviderUnavailableError(httpx.HTTPError):
    """Raised when a provider circuit breaker is OPEN.

    Subclasses ``httpx.HTTPError`` so existing ``except httpx`` handlers in
    callers treat it like any other provider failure. Carries ``status_code``
    (503) and ``retry_after`` so a route/handler can surface a proper
    ``Retry-After`` header; the app's HTTPException handler already forwards
    ``headers`` when the value is re-raised as an HTTPException upstream.
    """

    status_code = 503

    def __init__(self, provider: str, *, retry_after: int) -> None:
        super().__init__(f"{provider} is temporarily unavailable (circuit open)")
        self.provider = provider
        self.retry_after = retry_after


# --- Circuit breaker (optional pybreaker dependency) ------------------------
#
# ``fail_max`` consecutive failures trip the breaker OPEN for ``reset_timeout``
# seconds, after which a single trial call (HALF-OPEN) decides whether to close
# it again. When pybreaker is absent we fall back to a no-op wrapper so the
# import — and every wrapped call — keeps working unchanged.

CIRCUIT_FAIL_MAX = 10
CIRCUIT_RESET_TIMEOUT = 30

try:  # pragma: no cover - exercised only when pybreaker is installed
    import pybreaker

    _PYBREAKER_AVAILABLE = True
except ImportError:  # pragma: no cover - the local venv may not have it yet
    pybreaker = None  # type: ignore[assignment]
    _PYBREAKER_AVAILABLE = False

_PYBREAKER_ASYNC_AVAILABLE = _PYBREAKER_AVAILABLE and bool(
    getattr(pybreaker, "HAS_TORNADO_SUPPORT", False)
)


def _build_breaker(name: str) -> Any:
    if not _PYBREAKER_ASYNC_AVAILABLE:
        return None
    # ``exclude`` callables flag exceptions that must NOT count as failures: a
    # 4xx (bad auth/payload) is a caller bug, not a provider outage, so it must
    # not trip the circuit and lock everyone else out.
    return pybreaker.CircuitBreaker(
        fail_max=CIRCUIT_FAIL_MAX,
        reset_timeout=CIRCUIT_RESET_TIMEOUT,
        exclude=[lambda exc: not is_transient_error(exc)],
        name=name,
    )


_breakers: dict[str, Any] = {}


def _breaker_for(provider: str) -> Any:
    breaker = _breakers.get(provider)
    if breaker is None and _PYBREAKER_ASYNC_AVAILABLE:
        breaker = _build_breaker(provider)
        _breakers[provider] = breaker
    return breaker


def with_circuit_breaker(
    provider: str,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Wrap an async provider call in a per-provider circuit breaker.

    No-op when pybreaker's async support is unavailable. Pybreaker implements
    ``call_async`` via Tornado's ``gen`` helper, so calling it without Tornado
    raises ``NameError: name 'gen' is not defined`` before the provider request
    is even made. When the circuit is OPEN, raises :class:`ProviderUnavailableError`
    (HTTP 503 + Retry-After) instead of attempting the doomed call.
    """

    def _decorate(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        if not _PYBREAKER_ASYNC_AVAILABLE:
            return fn

        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> T:
            breaker = _breaker_for(provider)
            if breaker is not None and breaker.current_state == pybreaker.STATE_OPEN:
                logger.warning("Circuit breaker OPEN for provider %s; short-circuiting", provider)
                raise ProviderUnavailableError(provider, retry_after=CIRCUIT_RESET_TIMEOUT)
            try:
                return await breaker.call_async(fn, *args, **kwargs)
            except pybreaker.CircuitBreakerError as exc:
                # Breaker tripped mid-call (or open under a race): map to 503.
                logger.warning("Circuit breaker rejected call to provider %s", provider)
                raise ProviderUnavailableError(provider, retry_after=CIRCUIT_RESET_TIMEOUT) from exc

        return _wrapper

    return _decorate


def resilient_call(
    provider: str,
    *,
    max_attempts: int | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Compose retry + circuit breaker for an outbound provider call.

    Order matters: the breaker wraps the *retry* loop so a burst of retries on
    one request counts as a single logical attempt against the provider, and an
    already-open breaker short-circuits before any retry happens.
    """

    def _decorate(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        retried = with_async_retry(fn, max_attempts=max_attempts)
        return with_circuit_breaker(provider)(retried)

    return _decorate
