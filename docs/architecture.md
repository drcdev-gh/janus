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
| `SYNC_INTERVAL_SECONDS` | Normal background sync interval (default: 1800) |
| `FORCE_SYNC_INTERVAL_SECONDS` | Force sync interval within the background task (default: 10800) |
| `DRY_RUN` | Set to `1`, `true`, or `yes` to skip all Outline write operations and run only the startup sync |

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

A single `_scheduled_sync(last_force_sync)` task runs in an `asyncio.Task` started from
the FastAPI `lifespan` context. It wakes every `SYNC_INTERVAL_SECONDS` and calls
`_run_sync(force=...)` via `run_in_executor(None, ...)` to avoid blocking the event loop.

The task decides force vs normal each tick by comparing elapsed time since `last_force`
against `FORCE_SYNC_INTERVAL_SECONDS`. Force and normal syncs never overlap — the tick
is either one or the other. `last_force_sync` is initialised to the startup time (since
the lifespan runs a force sync before starting the task), so the first scheduled force
fires ~3 hours after startup.

---

## Routes

### `GET /health`
- Unauthenticated
- Three checks: `pocketid` (TCP reachability), `outline` (TCP reachability), `last_sync` (outcome of last sync)
- TCP ping opens a socket connection to the host/port from `POCKETID_API_URL` / `OUTLINE_API_URL` — no HTTP request, no API key used
- Response cached for 60 seconds to avoid hammering upstream hosts
- HTTP 200 when all checks are `"ok"`, 503 if any check fails; body always includes `checks` detail
- `last_sync_error` is set by the startup sync and the background task; `None` = last sync succeeded

### `POST /outline/force-sync`
- Auth: `x-api-key` header (constant-time comparison via `hmac.compare_digest`)
- Forces a full PocketID fetch and always runs the Outline sync pipeline regardless of whether data changed
- Returns `{"status": "ok"}` on success, 404 if the PocketID store or group list is empty

### `GET /ssh/validate`
- Auth: `x-api-key` header
- Query param: `pubkey` (max 8192 chars)
- Uses cached PocketID user store (refreshes if stale)
- Returns the matched public key as plain text with a trailing newline, or HTTP 204 if no match

---

## Outline sync pipeline (`_run_sync(force)`)

When `force=False`, `_run_sync` returns early (without touching Outline) if
`update_pocket_userstore` reports no change. When `force=True` (startup, 3-hour tick,
`/outline/force-sync`), the full pipeline always runs.



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

1. Staleness check — if `last_updated_timestamp` is `None` or older than
   `SYNC_INTERVAL_SECONDS * 1.1`, the request is rejected (HTTP 204) immediately.
   The 1.1× grace window avoids false rejections when the SSH check races the
   background sync timer by a few seconds.
2. `ssh.validate_keyformat(pubkey)` — regex check for valid key type and base64 body
3. Walk `pocket_userstore`; for each user in `SSH_ALLOWED_GROUP`, check the `ssh-pubkey`
   custom claim against the presented key
4. Return the matched key string on success, `None` on failure

The endpoint never triggers a PocketID fetch inline; it reads the cache maintained by
the background sync task.

---

## Dry-run mode

When `DRY_RUN` is set, janus runs a single startup sync (fetching real data from PocketID and Outline) but skips all Outline write operations — group creates/deletes, membership changes, and user suspend/reactivate. Per-operation log lines still fire so the operator can see what would change; summary lines append `(dry run)`. No `_scheduled_sync` task is started. The `/ssh/validate` endpoint returns HTTP 204 immediately without performing any validation.

`DRY_RUN` is a module-level boolean in both `outline.py` and `main.py`, set once at import time from the env var.

## Logging

Every module logs to `logging.getLogger("uvicorn")`.

Key log events during a sync:
- `pocket.py`: "Fetched N users from PocketID", "Found N groups in PocketID"
- `outline.py`: "Fetched N users from Outline across N groups", per-operation lines for each group/membership/status change, then summary lines: "Groups: N created", "Groups: N deleted", "Group memberships: N added, N removed", "User status: N suspended, N reactivated"
- `main.py`: sync start/complete/skipped/failed at each trigger point (startup, scheduled, manual)
- `ssh.py`: key validation outcomes (auth granted/denied, invalid format)

## Test patterns

- `tests/conftest.py` sets all required env vars before any module import (modules read env at import time)
- Route tests use `TestClient(main.app)` with `unittest.mock.patch`
- Unit tests call module functions directly with mocked dependencies
- State reset: tests that rely on `main.pocket_userstore` / `main.last_updated_timestamp`
  reset them in `setup_method`
- No live HTTP calls — all `requests.get` / `requests.post` calls are patched
