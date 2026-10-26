# Agent identity and API key: manual procedure

How to give one external AI agent its own least-privilege Aegis identity and API key,
and how to list, revoke and rotate that key. Everything here is done by people through
the platform's existing API (or its admin screens). The tool server itself holds no keys
and has no admin function (FR-39).

All paths are relative to the API base `https://<host>/api/v1` (locally
`http://localhost:8000/api/v1`). `<TOKEN>` is a normal sign-in access token. Every value in
angle brackets or written `clm_REDACTED` is a placeholder; never paste a real key into a
ticket, chat, repository or log.

Each step below was checked against the routes and services as they stand after T008. Where
something could not be confirmed in code, it is marked **Unverified**.

## 1. Principle

One dedicated Aegis user per agent, holding only `intake:create`. A key can do everything
its user can do, so never mint an agent key from a person's account and never share an
identity or key between agents.

## 2. Prerequisites

- An administrator who holds all of: `admin_panel:access` (create roles), `user:approve`
  (invite, list and revoke keys of other users), `user:update_role` (assign roles) and
  `screen_access:manage` (screen grants). Assigning a role also requires the administrator to
  hold every permission the role carries (`intake:create` here). The seeded built-in admin
  account held all of them: QA followed this procedure end to end with it (verification.md).
- The platform must run with RBAC enforced: `DISABLE_RBAC` must be `false`. The dev override
  (`docker-compose.override.yml`) sets `DISABLE_RBAC=true`, which makes every signed-in user an
  organisation admin, so on a dev stack the least-privilege checks below will wrongly pass.
  Do the verification in step 8 on a stack with RBAC on. The production settings check refuses
  to boot with `DISABLE_RBAC` set.
- A mailbox or alias the organisation controls for the agent, for example
  `agent-<name>@<your domain>`.
- A secrets vault for the agent's password and key.

## 3. One time: create the role `intake_agent`

Signed in as the administrator:

```
POST /roles
{"name": "intake_agent",
 "description": "External AI agents: file + follow own intake requests",
 "permissions": ["intake:create"]}
```

Returns 201 with the role `id`; note it as `<ROLE_ID>`. A second call with the same name returns
409. Check with `GET /roles` that the role lists exactly `["intake:create"]` (`GET /roles/permissions`
shows the catalogue).

Never add `intake:read`, `intake:update` or `admin_panel:access` to this role (FR-43). The
`intake:create` permission is what lets the key call the intake routes the tool server uses
(`GET /intake/forms`, `GET /intake/requests/mine`, `GET /intake/requests/{id}`, its `/sla` and
`/documents`, `POST /intake/requests/{id}/documents`), `GET /legal-entities` and
`GET /counterparties` (the latter two are gated by `intake:create`; QA confirmed 200 for an agent
key). Two routes share the `/counterparties` path: the `parties` one (`intake:create`) and the
`contracts` one (`contract:read`). The `parties` route serves it only because its router is
registered first in `app/main.py`; if that order changes, agents lose the register lookups.

## 4. One time: the `intake` screen grant (required for filing)

`POST /intake/requests` is also gated by a screen-level check (`intake` at ADD). A role holding
only the permission gets 403 on filing until it has a screen grant. Do **not** use the
bootstrap-access helper for this: it would grant DELETE.

1. Find the screen id (needs `screen_access:read`):

   ```
   GET /screens?module=Workspace
   ```

   Pick the entry whose `code` is `intake` (name "Legal Intake"); note its `id` as `<SCREEN_ID>`.

2. Create the grant (needs `screen_access:manage`):

   ```
   POST /screen-access/grants
   {"role_id": "<ROLE_ID>", "screen_id": "<SCREEN_ID>", "org_unit_id": null, "action_level": "ADD"}
   ```

   Returns 201. A repeat returns 409 ("A live grant already exists"). `action_level` is one of
   `VIEW`, `ADD`, `EDIT`, `DELETE`; use `ADD` and nothing higher. `org_unit_id: null` makes the
   grant organisation-wide. Check with `GET /screen-access/grants?role_id=<ROLE_ID>`.

## 5. Per agent: invite and accept

1. As the administrator:

   ```
   POST /users/invitations
   {"email": "agent-<name>@<your domain>", "role_name": "intake_agent"}
   ```

   `role_name` must already exist (step 3); `expires_in_days` is optional (1 to 30, default 7).
   The response includes the one-time invitation `token` (and `email_sent`, which is false when
   email is not configured). Hand the token only to whoever will accept it.

2. Accept it, unauthenticated (rate limited):

   ```
   POST /auth/invitations/accept
   {"token": "<INVITATION_TOKEN>", "full_name": "Agent: <name>", "password": "<long random, 10+ chars>"}
   ```

   The response is a token pair with `user_id`; note it as `<AGENT_USER_ID>`. The full name is what
   staff see against requests the agent files (FR-48), so name it clearly as an agent. Store the
   password in the vault.

## 6. Per agent: assign the role grant

Accepting an invitation only sets the user's role in a legacy column; it does not write the role
grant row that permission checks read (plan finding F1). Assign it explicitly, as the
administrator:

```
PUT /roles/user/<AGENT_USER_ID>
{"role_ids": ["<ROLE_ID>"], "active_role_id": "<ROLE_ID>"}
```

Returns the updated user. Unknown role ids return 422. `role_ids` replaces the user's role set,
so list every role they should hold (here, only `intake_agent`).

## 7. Per agent: mint the key

Sign in as the agent with email and password (a normal login session, not a key):

```
POST /auth/login
{"email": "agent-<name>@<your domain>", "password": "<agent password>"}
```

Then, with that `access_token` as the Bearer token:

```
POST /users/api-keys
{"name": "<agent> <yyyy-mm-dd>"}
```

Returns 201 with `id` (the key id, needed for revocation) and `api_key` (`clm_REDACTED` in this
document). **The raw key is shown once and cannot be retrieved again.** Put it in the vault for
the agent operator. A call authenticated with a key returns 403 here: keys cannot mint keys.

Finally sign out of the session: `POST /auth/logout` (needs the access token; the body carries a
`refresh_token` field when the refresh token is not in a cookie).

The key is sent by the agent as `Authorization: Bearer clm_REDACTED` to `https://<host>/mcp`.
The tool server stores nothing.

## 8. Verify

With RBAC enforced:

1. As the agent (access token): `GET /auth/me` lists `permissions: ["intake:create"]`.
2. With the key, through the MCP endpoint or directly: list forms, file a test request and read it
   back (`GET /intake/forms`, `POST /intake/requests`, `GET /intake/requests/mine`).
3. Staff routes must refuse the key: `GET /intake/requests` (the queue), `GET /roles`,
   `GET /users/invitations` all return 403.
4. If step 2 filing returns 403 while `GET /intake/forms` works, the screen grant (step 4) is
   missing or below ADD.
5. Keys cannot mint keys: `POST /users/api-keys` with the key returns 403.

## 9. List and revoke (incident or offboarding)

An administrator, signed in as themselves (needs `user:approve`):

```
GET  /users/<AGENT_USER_ID>/api-keys        # id, name, last_used_at, revoked_at; never the key value
POST /users/api-keys/<KEY_ID>/revoke        # returns the key with revoked_at set
```

The next call with that key returns 401, including inside an open agent session (FR-41). When in
doubt, revoke every key of the identity. To remove the identity's access, also remove its
role with `PUT /roles/user/<AGENT_USER_ID>` using an empty `role_ids`. **No API route deactivates
a user today** (`POST /users/{id}/approval` only decides users still pending approval). After
revoking the keys and removing the role, the agent user remains an active account with no
permissions that can still sign in; removing the account itself needs a database or admin action
outside the API. The agent itself can
list its own keys (`GET /users/api-keys`) and revoke them (`POST /users/api-keys/<KEY_ID>/revoke`),
using a normal login session.

## 10. Rotate

1. Sign in as the agent and mint a new key (step 7) with a new dated name.
2. Switch the agent to the new key.
3. Confirm the new key works, then revoke the old one (step 9). Check `last_used_at` on the old key
   has stopped moving.

## 11. What this does not provide

- No scopes: a key carries all of its user's permissions, which is why the role is minimal.
- No expiry: a key works until revoked. Rotate on a schedule you set.
- No administrator "issue key for user" action: a key can only be minted by the identity's own
  login session (FR-39). The administrator can list and revoke, not create.
- No separate "keys" admin screen is described here: use the API calls above.

## 12. What the audit trail shows

- `user.invitation_created`, `user.invitation_accepted`, `role.created`, `screen_access.granted`:
  the administrator or the accepting agent as actor.
- `api_key.created`: actor is the agent identity; metadata has `key_user_id`, `key_name` and
  `auth_method`. Because the agent mints its own key, the actor is the agent, not an administrator
  (risk R2). Tie the minting to a person through your own change ticket.
- `api_key.revoked`: actor, `key_user_id`, `key_name`, `revoked_by_admin`.
- `intake.created` and `intake.document.added`: record `auth_method: api_key` and the `api_key_id`.
- The key value or hash is never written.

## 13. Never

- Share one identity or key between agents.
- Put a key in tool-server configuration, compose files, environment files, logs or tickets.
- Widen the `intake_agent` role, or raise its `intake` grant above ADD.
- Mint an agent key from a person's account.
