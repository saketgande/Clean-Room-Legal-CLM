"""Environment configuration. Everything here is non-secret: the caller's API key arrives
on each request and is never part of the server's configuration (FR-11)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_HOSTS = "localhost:8001,127.0.0.1:8001,mcp:8001"


class ConfigError(ValueError):
    """An environment variable holds a value the server cannot run with."""


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0, got {raw!r}")
    return value


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    value = _number(env, name, float(default))
    if value != int(value):
        raise ConfigError(f"{name} must be a whole number, got {env.get(name)!r}")
    return int(value)


@dataclass(frozen=True)
class Config:
    api_base_url: str = "http://backend:8000/api/v1"
    health_url: str = "http://backend:8000/healthz"
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    write_timeout_seconds: float = 45.0
    attempts: int = 2
    tool_deadline_seconds: float = 110.0
    triage_poll_seconds: int = 30
    max_body_bytes: int = 62_914_560
    allowed_hosts: tuple[str, ...] = tuple(_DEFAULT_HOSTS.split(","))
    host: str = "0.0.0.0"  # noqa: S104 - container bind; exposure is decided by compose/nginx
    port: int = 8001
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        hosts = tuple(
            h.strip() for h in e.get("MCP_ALLOWED_HOSTS", _DEFAULT_HOSTS).split(",") if h.strip()
        )
        return cls(
            api_base_url=e.get("AEGIS_API_BASE_URL", cls.api_base_url).rstrip("/"),
            health_url=e.get("AEGIS_HEALTH_URL", cls.health_url),
            connect_timeout_seconds=_number(e, "AEGIS_CONNECT_TIMEOUT_SECONDS", 5.0),
            read_timeout_seconds=_number(e, "AEGIS_READ_TIMEOUT_SECONDS", 15.0),
            write_timeout_seconds=_number(e, "AEGIS_WRITE_TIMEOUT_SECONDS", 45.0),
            attempts=_integer(e, "AEGIS_ATTEMPTS", 2),
            tool_deadline_seconds=_number(e, "MCP_TOOL_DEADLINE_SECONDS", 110.0),
            triage_poll_seconds=_integer(e, "MCP_TRIAGE_POLL_SECONDS", 30),
            max_body_bytes=_integer(e, "MCP_MAX_BODY_BYTES", 62_914_560),
            allowed_hosts=hosts,
            host=e.get("MCP_HOST", cls.host),
            port=_integer(e, "MCP_PORT", 8001),
            log_level=e.get("LOG_LEVEL", cls.log_level).upper(),
        )
