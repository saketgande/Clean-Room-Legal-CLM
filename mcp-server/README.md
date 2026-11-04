# Aegis Intake MCP server

A small, stateless [MCP](https://modelcontextprotocol.io) tool server that lets an external AI
agent file and follow legal-intake requests in Aegis. It is a thin proxy over the Aegis REST API:
no database, no stored secrets, no state between calls. Each call is made with the **calling
agent's own Aegis API key**, so the platform's normal permissions decide what the agent can see
and do.

To create the agent's identity and key, follow [AGENT_IDENTITY.md](AGENT_IDENTITY.md) (a
least-privilege user plus an API key).

## The seven tools

| Tool | What it does |
|---|---|
| `list_request_forms` | The agreement forms and every question, with register options for entity/counterparty fields |
| `create_intake_request` | File a request (answers, optional attachments, idempotency key) |
| `get_intake_request` | Current state of one request this agent filed (id or `REQ-…` reference) |
| `list_my_requests` | Requests filed by this agent's own identity, newest first |
| `get_request_sla` | The SLA timeline for one of its requests, as Aegis reports it |
| `get_request_documents` | Documents on a request, optionally with extracted text |
| `add_document` | Attach one more document to a request (inline base64 only) |

An agent can only see requests filed under its own identity. All text returned from Aegis is
data written by people or other systems; the server tells the model never to treat it as
instructions.

## Configuration

All settings are environment variables, and none is a secret (the key arrives with each call).

| Var | Default | Purpose |
|---|---|---|
| `AEGIS_API_BASE_URL` | `http://backend:8000/api/v1` | The only upstream (FR-54) |
| `AEGIS_HEALTH_URL` | `http://backend:8000/healthz` | Readiness probe target |
| `AEGIS_CONNECT_TIMEOUT_SECONDS` | `5` | Per attempt |
| `AEGIS_READ_TIMEOUT_SECONDS` | `15` | Per attempt, read tools |
| `AEGIS_WRITE_TIMEOUT_SECONDS` | `45` | Per attempt, create / add_document |
| `AEGIS_ATTEMPTS` | `2` | Total attempts for reads and create (not add_document) |
| `MCP_TOOL_DEADLINE_SECONDS` | `110` | Hard cap per tool call |
| `MCP_TRIAGE_POLL_SECONDS` | `30` | `poll_after_seconds` while triage is pending |
| `MCP_MAX_BODY_BYTES` | `62914560` (60 MiB) | Largest request body; matches nginx `client_max_body_size 60m` |
| `MCP_ALLOWED_HOSTS` | `localhost:8001,127.0.0.1:8001,mcp:8001` | Allowed `Host` values (DNS-rebinding protection); production adds the public host |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8001` | Bind address and port |
| `LOG_LEVEL` | `INFO` | Logging level |

## Run it

**With the stack.** The compose service `mcp` is opt-in (compose profile `mcp`), so it does not
start with the rest of the stack. Enable it with `docker compose --profile mcp up -d mcp`, or set
`COMPOSE_PROFILES=mcp` so a plain `docker compose up` includes it. It publishes port 8001 on the
host's loopback only (`127.0.0.1:8001`), so the server is at `http://localhost:8001`.

**Locally from source** (Python 3.10+), against a platform on `localhost:8000`:

```bash
cd mcp-server
pip install -r requirements.txt
AEGIS_API_BASE_URL=http://localhost:8000/api/v1 \
AEGIS_HEALTH_URL=http://localhost:8000/healthz \
  uvicorn aegis_intake_mcp.server:app --port 8001
```

Endpoints:

* `POST /mcp`: the MCP endpoint (streamable HTTP, JSON responses, stateless).
* `GET /healthz`: 200 while the process is alive.
* `GET /readyz`: 200 only while the platform's `/healthz` answers (3 s timeout), else 503
  `{"status":"backend_unavailable"}`. Use this for container health checks.

Tests and lint, from `mcp-server/`: `python -m ruff check .` and `python -m pytest -q`. They are
hermetic: no network, no Docker.

## Connect an agent

Point an MCP client that supports streamable HTTP at `http://localhost:8001/mcp` and send the
key as a header:

```json
{
  "mcpServers": {
    "aegis-intake": {
      "type": "http",
      "url": "http://localhost:8001/mcp",
      "headers": { "Authorization": "Bearer clm_your_key_here" }
    }
  }
}
```

`tools/list` works without a key so a client can discover the tools first. Every `tools/call`
needs the header; without a valid `Bearer clm_…` the tool returns `authentication_failed` and
nothing is sent to Aegis.

## TLS

The server speaks plain HTTP. Terminate TLS at nginx and proxy `/mcp` to the `mcp` service
(`deploy/nginx/aegis.conf` has an exact `location = /mcp` with `client_max_body_size 60m`,
proxying to `127.0.0.1:8001`). Add the public host name to `MCP_ALLOWED_HOSTS`, otherwise
requests with that `Host` are refused.

Operator notes for production:

* **Reach the tool server only through nginx TLS or loopback.** Agents send their Bearer key
  here, so compose publishes the port as `127.0.0.1:8001` and nothing should expose 8001 on
  another interface.
* **The backend's `ALLOWED_HOSTS` must include `backend`.** The tool server calls the platform
  as `http://backend:8000`; once a non-wildcard `ALLOWED_HOSTS` is set, a list without
  `backend` makes the backend's TrustedHostMiddleware answer those calls with 400.

## Warning: dev stack runs with `DISABLE_RBAC=true`

The development compose stack sets `DISABLE_RBAC=true`, so in dev **every key can do and see
everything its user could, regardless of permissions**. A least-privilege agent identity
therefore cannot be proven in dev. Verify isolation (an agent sees only its own requests, and is
refused anything outside its permissions) with `DISABLE_RBAC` **off** before relying on it.

## Offline and outbound traffic

The server makes no outbound calls except to the Aegis platform (`AEGIS_API_BASE_URL`, and the
`AEGIS_HEALTH_URL` probe). It honours no proxy environment variables and follows no redirects,
so a key cannot be sent anywhere else. It needs no internet access. AI triage of filed requests
runs inside the platform and depends on the platform's own model configuration; when that is
unavailable, requests stay in the `pending` triage state rather than being guessed.

## Security notes

* The key is forwarded for that one call and never stored: not in memory between calls, a cache,
  a file or a log. A filter scrubs `clm_…` tokens and Authorization values from every log line.
* Logs hold the tool name, outcome, timing and platform request id only; never arguments,
  attachment content or headers.
* The tool surface is curated: nine fixed platform calls, nothing else is reachable.
* Request bodies over 60 MiB are refused with 413; attachments are checked (count, size, type,
  base64) before anything is sent.
* The container runs as a non-root user with a read-only root filesystem.

## Troubleshooting

* **`authentication_failed`**: the `Authorization: Bearer clm_…` header is missing or malformed
  (nothing was sent to Aegis), or Aegis rejected the key (wrong, revoked, or its user was
  deactivated). Check the client config and the key in Admin.
* **`rate_limited`** with `retry_after_seconds`: Aegis is limiting the key. Wait that long and
  try again; nothing was filed.
* **`outcome_unknown`**: Aegis did not confirm a write. For `create_intake_request` the error
  carries `idempotency_key`; call the tool again with the same key and Aegis returns the
  original request instead of filing twice. For `add_document`, call `get_request_documents`
  first.
* **`service_unavailable`**: Aegis could not be reached; nothing was done. Check `/readyz`.
* **Requests refused with 421/400**: the `Host` header is not in `MCP_ALLOWED_HOSTS`.
