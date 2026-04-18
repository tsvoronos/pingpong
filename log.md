# AGP-0001 Implementation Log — Connectors framework + Panopto

Branch: `feat/connectors-panopto` (off `upstream/main` @ `ac6c56f`).

## Scope delivered

The OAuth lifecycle from the plan: connect → store token → refresh → disconnect.
No assistant-side wiring, no retrieval, no ingestion (all deferred per the plan's
"Out of scope" section).

## Files created

- [pingpong/connectors/__init__.py](pingpong/connectors/__init__.py) — module
  exports + singleton registry (`register`, `get`, `all_connectors`).
- [pingpong/connectors/base.py](pingpong/connectors/base.py) —
  `OAuth2Connector` base class (`build_authorize_url`, `exchange_code`,
  `refresh`, `revoke`, `get_access_token` with auto-refresh).
- [pingpong/connectors/panopto.py](pingpong/connectors/panopto.py) —
  `PanoptoConnector`, OIDC-discovery-backed with an in-memory per-host cache.
- [pingpong/connectors/exceptions.py](pingpong/connectors/exceptions.py) —
  `ConnectorError`, `ConnectorNotConfigured`, `ConnectorNotRegistered`,
  `TokenRefreshError`, `OAuthStateError`.
- [pingpong/connectors/state.py](pingpong/connectors/state.py) — OAuth
  state-JWT encode/decode using the existing auth secret (see "Assumption #1"
  below).
- [alembic/versions/337b7d1fe811_add_user_connectors.py](alembic/versions/337b7d1fe811_add_user_connectors.py)
  — `user_connectors` table migration, revision `337b7d1fe811`, down to
  `b4f9d7e21c5a`.
- [pingpong/test_connectors_base.py](pingpong/test_connectors_base.py),
  [pingpong/test_connectors_panopto.py](pingpong/test_connectors_panopto.py),
  [pingpong/test_connectors_server.py](pingpong/test_connectors_server.py) —
  unit + integration tests.

## Files modified

- [pingpong/config.py](pingpong/config.py) — added
  `PanoptoTenantSettings` / `PanoptoConnectorSettings` /
  `ConnectorsSettings`, wired into `Config.connectors`. Defaults to an empty
  tenants list so existing configs keep working.
- [pingpong/models.py](pingpong/models.py) — added `UserConnector`
  class + `User.connectors` relationship.
- [pingpong/schemas.py](pingpong/schemas.py) — added
  `ConnectorSummary`, `ConnectorTenantOption`, `ConnectorDefinition`,
  `ConnectorsListResponse`, `ConnectorConnectRequest`,
  `ConnectorConnectResponse`, `ConnectorDisconnectResponse`.
- [pingpong/server.py](pingpong/server.py) — 4 routes next to
  `/me/external-logins`:
  - `GET /api/v1/me/connectors`
  - `POST /api/v1/connectors/{service}/connect`
  - `GET /api/v1/connectors/{service}/callback`
  - `DELETE /api/v1/me/connectors/{connector_id}`
- [test_config.toml](test_config.toml) — added `connectors.panopto.tenants`
  entry (`tenant=demo`) so tests have a working tenant without hitting a live
  Panopto server.
- [web/pingpong/src/lib/api.ts](web/pingpong/src/lib/api.ts) —
  `ConnectorSummary` / `ConnectorDefinition` types + `getMyConnectors`,
  `connectConnector`, `disconnectConnector` helpers.
- [web/pingpong/src/routes/profile/+page.ts](web/pingpong/src/routes/profile/+page.ts)
  — loads connectors alongside external logins.
- [web/pingpong/src/routes/profile/+page.svelte](web/pingpong/src/routes/profile/+page.svelte)
  — Service Connectors section beneath External Logins, per-tenant rows
  with Connect/Disconnect buttons and a "needs reauth" badge when the
  refresh token is missing.

## Deviations / assumptions

**Assumption 1 — OAuth state JWT signed directly, not via
`encode_auth_token()`.** The plan calls out the existing
`encode_auth_token()` helper at `pingpong/auth.py:44-82`, but that helper
wraps a fixed `AuthToken` Pydantic schema (`sub` / `iat` / `exp` only) and
can't carry the extra `service` / `tenant` / `pkce_verifier` claims the
state needs. I added [pingpong/connectors/state.py](pingpong/connectors/state.py)
which reuses the same secret (`config.auth.secret_keys[0]`) and algorithm
but encodes / decodes a richer payload directly via `jwt.encode` /
`jwt.decode`. The security property — HS256 signature over the existing
secret with `exp` verification — matches `encode_auth_token`'s.

**Assumption 2 — `httpx` for outbound OAuth calls, not `aiohttp`.** The
existing Canvas OAuth flow (`pingpong/lti/canvas_connect.py`) uses
`aiohttp` because it needs the redirect-validation `trace_config` machinery
from the LTI security settings. Connectors don't have that requirement —
the URLs they hit come straight from OIDC discovery on the configured
tenant host, so the extra machinery is unnecessary weight. `httpx` was
already in `pyproject.toml` (`httpx ~= 0.28.1`), so no new dependency.

**Assumption 3 — no `raise_for_status()` on the revoke call.** My first
attempt called `resp.raise_for_status()` and wrapped it in `try / except
httpx.HTTPError`; testing showed `raise_for_status()` requires a `request`
attribute on the response, which synthetic `httpx.Response` instances in
tests don't have. Since `revoke()` is best-effort anyway — we always
delete the DB row regardless — I dropped the status check entirely and
just let any exception fall into the `except httpx.HTTPError: pass`
branch.

**Assumption 4 — `tenant: str | None` in the state JWT, with the PKCE
verifier allowed to be missing.** The plan says state "embeds user_id,
service, tenant, and PKCE verifier." The decoder in
[pingpong/connectors/state.py](pingpong/connectors/state.py) treats
`tenant` and `pkce_verifier` as optional so a future non-PKCE / non-multi-
tenant connector (e.g. Zotero) doesn't have to fake up values. Only `sub`
and `service` are strictly required.

**Assumption 5 — "needs_reauth" surfacing.** The plan says the summary
status is `"active" | "needs_reauth"` but doesn't specify which condition
produces each. I mapped `needs_reauth` to "no `refresh_token` stored"
(i.e. we can't transparently refresh without kicking the user back
through OAuth). The status is recomputed on every list call, so the
classification updates automatically when a refresh fails and the token
is cleared.

**Assumption 6 — callback redirects on failure.** The plan specifies the
success redirect (`/profile?connected={service}`) but not the failure
shape. I redirect to `/profile?connector_error=<code>` with
`<code>` in `{missing_params, bad_state, service_mismatch, unknown_service,
exchange_failed, <provider-error-code>}`. The frontend ignores these
today but they're there for the follow-up UX PR to pick up.

## Issues hit during implementation

**Issue 1 — stale line numbers in the plan.** The plan references
`/me/external-logins` at `server.py:11519` and `update_me` at
`server.py:11512`; in current `main` they're at 11836 and 11824. I
located them via the `@v1.get("/me/external-logins"` anchor instead.
Same story with the plan's "lines 280-328" for the External Logins
section of `+page.svelte` — still accurate, no-op. Only the server.py
line numbers drifted.

**Issue 2 — no live Postgres or Docker at first.** The plan's verification
step 5 initially couldn't run against the configured Postgres URL.
Initially proven on SQLite via the alembic Python API. After Docker
became available, re-run end-to-end on real Postgres 15.5 (see
"Verification → Migration reversibility on Postgres" below). No issues;
schema, indexes, FK, and unique constraint all present; inserts exercise
server defaults and the unique constraint correctly.

**Issue 3 — `PanoptoConnector` is registered once at import time with an
in-memory discovery cache that lives for the process.** The integration
tests can stale-match cached responses from a prior test if they share
the same host. I added an autouse fixture in
[pingpong/test_connectors_server.py](pingpong/test_connectors_server.py)
that clears `connectors_pkg.get("panopto")._discovery_cache` before and
after every test.

**Issue 4 — `config.py` singleton makes tests read the same
`test_config.toml` that prod code does.** Adding a `connectors.panopto`
tenant (`tenant = "demo"`) to `test_config.toml` rather than fixtures was
the simplest way to make the Panopto-specific test cases configurable.
This mirrors what the existing `[[lms.lms_instances]]` block does for the
Canvas tests. If a future test needs a different tenant layout, the right
move will be to refactor the config fixture to allow overrides.

**Issue 5 — `pnpm lint` caught whitespace in the Svelte block.**
Prettier re-wrapped one line in `+page.svelte`; I ran `pnpm format` and
re-ran `pnpm lint` clean. No semantic changes.

## Tests run

### Connector unit + integration suite (added in this PR)

```
CONFIG_PATH=test_config.toml uv run pytest \
    pingpong/test_connectors_base.py \
    pingpong/test_connectors_panopto.py \
    pingpong/test_connectors_server.py
```

Result: **35 passed** (13 base + 7 panopto + 15 server).

Coverage maps to the plan's verification section:

- Verify §1 (build_authorize_url / exchange_code / refresh / revoke /
  discovery caching) → `test_connectors_base.py` +
  `test_connectors_panopto.py`.
- Verify §2 (POST /connect returns host-matched URL with state that
  decodes back to user_id) →
  `test_connect_returns_authorize_url_with_signed_state`.
- Verify §3 (callback exchanges code, creates `UserConnector` row) →
  `test_callback_exchanges_code_and_upserts_row` +
  `test_callback_overwrites_existing_row_for_same_tenant`.
- Verify §4 (DELETE as non-owner 404s; owner deletes) →
  `test_disconnect_removes_row_when_owner` /
  `test_disconnect_returns_404_for_non_owner` /
  `test_disconnect_returns_404_for_missing_id`.

Extra coverage:

- `test_connect_requires_tenant_for_panopto` — 400 when tenant missing.
- `test_connect_rejects_unknown_tenant` — 400 when tenant not configured.
- `test_connect_rejects_unknown_service` — 404 for `/connectors/unknown/connect`.
- `test_callback_rejects_bad_state` / `test_callback_rejects_service_mismatch`
  / `test_callback_propagates_provider_error` — error redirect branches.
- `test_list_connectors_unauthenticated_is_403` — auth gate.
- `test_refresh_reuses_refresh_token_when_provider_omits_it` — spec-
  compliant providers that omit `refresh_token` on refresh don't clear
  ours.
- `test_get_access_token_refreshes_when_expired` /
  `test_get_access_token_returns_existing_when_fresh` — threshold
  behavior + DB write-through.

### Full backend suite (regression check)

```
CONFIG_PATH=test_config.toml uv run pytest -q
```

Result: **806 passed in 503s** — no regressions in the existing suite.

### Frontend

```
cd web/pingpong && pnpm check        # svelte-check
cd web/pingpong && pnpm lint         # prettier + eslint
cd web/pingpong && pnpm test         # vitest
```

Results:
- `pnpm check` → 2427 files, 0 errors, 0 warnings.
- `pnpm lint` → clean (after one `pnpm format` pass).
- `pnpm test` → 35 passed (no new tests — the page logic is too
  thin to be worth a unit test at this PR scope).

### Migration reversibility on SQLite (initial pass)

```
CONFIG_PATH=test_config.toml uv run python - <<'PY'
from alembic import command; from alembic.config import Config
cfg = Config('alembic.ini')
cfg.set_main_option('sqlalchemy.url', 'sqlite:///test_migration_check.sqlite')
command.stamp(cfg, 'b4f9d7e21c5a')
command.upgrade(cfg, '337b7d1fe811')    # → user_connectors exists
command.downgrade(cfg, 'b4f9d7e21c5a')  # → user_connectors gone
command.upgrade(cfg, '337b7d1fe811')    # re-upgrade clean
PY
```

Result: upgrade → schema assertion → downgrade → table-removed
assertion → upgrade, all OK on SQLite.

### Migration reversibility on Postgres 15.5

After Docker became available I re-ran against a real Postgres:

```
docker run -d --name pp-migration-db \
    -e POSTGRES_USER=pingpong -e POSTGRES_PASSWORD=pingpong \
    -e POSTGRES_DB=pingpong -p 5432:5432 \
    --platform linux/amd64 postgres:15.5
# create a fresh DB for the test
docker exec pp-migration-db psql -U pingpong -d pingpong \
    -c "CREATE DATABASE pingpong_migration_test;"
# use pingpong's CLI so alembic.ini's URL gets overridden from config
CONFIG_PATH=/tmp/pg_migration_config.toml uv run python -m pingpong db init
CONFIG_PATH=/tmp/pg_migration_config.toml uv run python -m pingpong db migrate --downgrade b4f9d7e21c5a
CONFIG_PATH=/tmp/pg_migration_config.toml uv run python -m pingpong db migrate   # -> head
# round 2
CONFIG_PATH=/tmp/pg_migration_config.toml uv run python -m pingpong db migrate --downgrade b4f9d7e21c5a
CONFIG_PATH=/tmp/pg_migration_config.toml uv run python -m pingpong db migrate   # -> head
```

Result on Postgres:
- `db init` creates 68 tables (including `user_connectors`) and stamps to
  `337b7d1fe811`.
- `\d user_connectors` shows the expected 12 columns, PK on `id`, index
  `idx_user_connectors_user_id`, unique `uq_user_service_tenant
  (user_id, service, tenant)`, and FK to `users.id`.
- Downgrade → table is gone; `alembic_version` == `b4f9d7e21c5a`.
- Re-upgrade → table back with same schema; `alembic_version` ==
  `337b7d1fe811`.
- Second downgrade/upgrade cycle also clean.
- Runtime integrity: inserting `(user_id=1, service='panopto',
  tenant='harvard', ...)` succeeds, `created` / `updated` populate via
  `server_default=now()`, a duplicate insert with the same
  `(user_id, service, tenant)` rejects with
  `duplicate key value violates unique constraint "uq_user_service_tenant"`.

CLI note: raw `alembic downgrade -1` failed with "Destination ... is
not a valid downgrade target" because `alembic.ini`'s `sqlalchemy.url`
hardcodes `localhost/pingpong`, which in this test container was the
empty maintenance DB (no `alembic_version` row). `python -m pingpong
db migrate` works because it calls `_load_alembic()` which overrides
the URL from `CONFIG_PATH`. Future migration verifications should
follow the same path.

## Live end-to-end test against Harvard Panopto

Tested with a real "Server-side Web Application" client
(`b5f73a83-92ec-4ba3-9621-b411010f4240`) registered at
`harvard.hosted.panopto.com`. Full round-trip succeeded:

1. Click **Connect** on `/profile` → `POST /connectors/panopto/connect`
   returns a `harvard.hosted.panopto.com/Panopto/oauth2/connect/authorize`
   URL.
2. User authenticates on Panopto, Panopto redirects back to
   `http://localhost:5173/api/v1/connectors/panopto/callback?code=...&state=...`
   (the callback host is `public_url` — Vite proxies `/api/*` to the
   backend on `:8000` in dev).
3. Backend exchanges the code → Panopto returns a valid token payload.
4. `user_connectors` row persists with tokens + scopes + expires_at.
5. UI flips to "Connected".
6. Click **Disconnect** → backend calls Panopto's
   `revocation_endpoint` (returns 200) → `DELETE /me/connectors/{id}`
   drops the row.

**Finding: Panopto rejects PKCE alongside `client_secret`.** The initial
attempt failed at the token exchange step with `{"error":"invalid_grant"}`
(no description). Panopto's Server-side Web Application flow
authenticates via `client_secret_post`; sending `code_verifier` +
`code_challenge` along with the secret is enough to make the token
endpoint refuse the code. Removing PKCE made the exchange succeed on
the next try.

To fix this without forcing every connector off PKCE, I added a
`use_pkce: ClassVar[bool] = True` class flag on `OAuth2Connector`, set
`use_pkce = False` on `PanoptoConnector`, and taught the `/connect`
route to skip PKCE generation when the flag is off. Updated tests —
`test_connect_returns_authorize_url_with_signed_state` now asserts the
authorize URL has no `code_challenge=` and the state JWT's
`pkce_verifier` is `None`.

**Redirect URI note for future Panopto setups.** The URI to register
in Panopto's "Allowed Redirect URLs" field is the `public_url`-rooted
callback, not the backend port — e.g. for local dev with
`public_url = "http://localhost:5173"`:

```
http://localhost:5173/api/v1/connectors/panopto/callback
```

Vite's proxy forwards `/api/*` to `:8000`, so this works even though
the backend listens on `:8000`.

## Not yet done / follow-ups
- Wire `user.connectors` into the user-deletion flow so it iterates +
  calls `revoke()` before deleting the rows (flagged in the plan; the
  deletion flow is explicitly out of scope for this PR).
- Token encryption at rest (flagged as cross-cutting tech debt in the
  plan — not part of this PR).
- All downstream features: retrieval / ingestion, folder picker UX,
  assistant-side wiring, student flow, MCP exposure.
