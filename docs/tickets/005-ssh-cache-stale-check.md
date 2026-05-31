# SSH login latency: read from shared cache, reject on stale

**Status:** done

## Goal

SSH logins are slow because `/ssh/validate` can trigger a blocking PocketID API fetch inline when the shared cache is stale. Remove the inline refresh — the endpoint reads from the existing shared `pocket_userstore` only. To preserve security, the endpoint rejects the login if the cache is older than `SYNC_INTERVAL_SECONDS * 1.1`, so a broken sync doesn't allow logins against stale access data. The 10% grace window avoids false rejections when the SSH check races the sync timer by a few seconds.

## Background

`/ssh/validate` calls `update_pocket_userstore(False)`, which hits the paginated PocketID `/api/users` endpoint if `pocket_userstore` is older than `SYNC_INTERVAL_SECONDS`. This blocks the SSH login until all pages are fetched.

The background task (`_scheduled_sync`) already refreshes `pocket_userstore` every `SYNC_INTERVAL_SECONDS` (default 30 min), and the startup sync populates it before the app accepts requests. The inline refresh in the SSH handler is therefore redundant in the happy path, and harmful to latency.

See `docs/architecture.md` — Caching and Background task sections.

## Scope

- Remove the `update_pocket_userstore(False)` call from `validate_ssh_login`
- Add a staleness check in `validate_ssh_login`: if `last_updated_timestamp` is `None` or older than `SYNC_INTERVAL_SECONDS * 1.1`, return HTTP 204 and log a warning
- No new env vars, no new background tasks, no new caches

## Out of scope

- Thread-safety / locking (deferred, noted in architecture.md)
- Changes to Outline sync scheduling or `SYNC_INTERVAL_SECONDS` defaults
- SSH audit logging or per-user metrics

## Proposed approach

**`main.py` — `validate_ssh_login`:**

```python
now = datetime.now(timezone.utc)
if (
    last_updated_timestamp is None
    or now - last_updated_timestamp > timedelta(seconds=SYNC_INTERVAL_SECONDS * 1.1)
):
    logger.warning("SSH login rejected: user cache is stale or empty")
    return PlainTextResponse("", status_code=204)

key = ssh.validate_pubkey(pubkey, pocket_userstore)
```

Remove the `update_pocket_userstore(False)` call entirely.

## Acceptance criteria

- [ ] `validate_ssh_login` makes no PocketID API call — reads cache only
- [ ] Login is rejected (HTTP 204) with a warning log if the cache is `None` or older than `SYNC_INTERVAL_SECONDS * 1.1`
- [ ] Login proceeds normally when the cache is fresh
- [ ] Tests cover: fresh cache allows validation; cache just within the 1.1× threshold is allowed; cache older than 1.1× threshold rejects without calling PocketID; empty cache rejects without calling PocketID
- [ ] All existing tests pass
