# janus — architecture reference

## Overview

janus is an SSO permission propagation daemon. It syncs user groups and claims from
[PocketID](https://github.com/pocket-id/pocket-id) to downstream services, and validates
SSH public keys for `AuthorizedKeysCommand` use. Currently supports Outline and SSH.

---

## Modules

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, PocketID user-store cache, scheduled sync loop, route handlers |
| `pocket.py` | PocketID API client — fetches users and groups via paginated GET |
| `outline.py` | Outline API client and full sync logic (groups, memberships, suspended status) |
| `ssh.py` | SSH public key format validation and PocketID claim matching |
| `tests/` | pytest suite — `unittest.mock` patches all HTTP; `TestClient` for routes |
| `test/ssh/` | Docker-based SSH server for manual integration testing |

---

## Environment variables

| Variable | Description |
|---|---|
| `POCKETID_API_URL` | Base URL of PocketID (must be `https://`) |
| `POCKETID_API_KEY` | PocketID admin API key |
| `OUTLINE_API_URL` | Base URL of Outline (must be `https://`) |
| `OUTLINE_API_KEY` | Outline API token |
| `SSH_ALLOWED_GROUP` | PocketID group whose members may log in via SSH |
| `API_KEY` | Shared secret for authenticating requests to janus |
| `SYNC_INTERVAL_SECONDS` | Background sync interval (default: 1800) |

Both `*_API_URL` values are validated at startup — the process exits if either is not `https://`.

---

## Data structures

### `pocket.PocketUser` (dataclass)
```python
username: str
user_id: str
email: str
groups: list[str]       # group names the user belongs to
custom_claims: list[dict]  # e.g. [{"key": "ssh-pubkey", "value": "ssh-ed25519 ..."}]
disabled: bool
```

### `outline.OutlineUser` (dataclass)
```python
id: str
name: str
email: str | None
groups: list[str]   # group names from Outline's groups.memberships API
suspended: bool
```

---

## Caching

`main.py` holds a module-level `pocket_userstore: list[PocketUser] | None` and
`last_updated_timestamp`. `update_pocket_userstore(force_update)` refreshes the store
if stale (older than `SYNC_INTERVAL_SECONDS`) or if forced. The `/ssh/validate` endpoint
uses the cache; `/outline/sync` always forces a refresh.

**Note:** the cache is not thread-safe. Safe with the default single Uvicorn worker;
requires a lock if running with multiple workers.

---

## Background task

`_scheduled_sync()` runs in a `asyncio.Task` started from the FastAPI `lifespan` context.
It sleeps for `SYNC_INTERVAL_SECONDS`, then runs `_run_sync()` via
`run_in_executor(None, _run_sync)` (thread pool) to avoid blocking the event loop.

---

## Routes

### `POST /outline/sync`
- Auth: `x-api-key` header (constant-time comparison via `hmac.compare_digest`)
- Forces a full PocketID fetch, then runs the Outline sync pipeline
- Returns `{"status": "ok"}` on success, 404 if the PocketID store or group list is empty

### `GET /ssh/validate`
- Auth: `x-api-key` header
- Query param: `pubkey` (max 8192 chars)
- Uses cached PocketID user store (refreshes if stale)
- Returns the matched public key as plain text with a trailing newline, or HTTP 204 if no match

---

## Outline sync pipeline (`_run_sync`)

1. Force-refresh PocketID user store
2. Fetch authoritative group list from PocketID (`/api/user-groups`)
3. Fetch Outline group list
4. `create_missing_groups` — create groups in Outline that exist in PocketID but not Outline
5. `delete_extra_groups` — delete groups in Outline that no longer exist in PocketID
6. `build_outline_user_store` — fetch all Outline users and their current group memberships
7. `sync_group_memberships` — add/remove memberships to match PocketID (matched by email)
8. `sync_suspended_status` — suspend/reactivate Outline users based on PocketID `disabled` flag

Steps 4–5 each return the updated group list so the pipeline never re-fetches from the API.

---

## SSH validation flow

1. `ssh.validate_keyformat(pubkey)` — regex check for valid key type and base64 body
2. Walk `pocket_userstore`; for each user in `SSH_ALLOWED_GROUP`, check the `ssh-pubkey`
   custom claim against the presented key
3. Return the matched key string on success, `None` on failure

---

## Test patterns

- `tests/conftest.py` sets all required env vars before any module import (modules read env at import time)
- Route tests use `TestClient(main.app)` with `unittest.mock.patch`
- Unit tests call module functions directly with mocked dependencies
- State reset: tests that rely on `main.pocket_userstore` / `main.last_updated_timestamp`
  reset them in `setup_method`
- No live HTTP calls — all `requests.get` / `requests.post` calls are patched
