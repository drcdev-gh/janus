# Health check: expose SSH cache staleness

**Status:** done

## Goal

The `/health` endpoint does not reflect whether SSH logins would currently succeed. After ticket 005, a stale `pocket_userstore` silently blocks all SSH logins with HTTP 204 — but the health check shows healthy. Add an `ssh_cache` check so operators and alerting catch a stuck sync before users notice broken SSH access.

## Background

`/health` runs three checks: `pocketid` (TCP ping), `outline` (TCP ping), and `last_sync` (last sync error string). `last_sync` only records whether the sync raised an exception — it does not track whether the resulting cache is fresh enough for SSH. The staleness threshold used by `/ssh/validate` is `SYNC_INTERVAL_SECONDS * 1.1` (from ticket 005). The health check should use the same threshold.

See `docs/architecture.md` — Routes `/health` and SSH validation flow.

## Scope

- Add `"ssh_cache"` key to `/health` checks: `"ok"` when `last_updated_timestamp` is within `SYNC_INTERVAL_SECONDS * 1.1`, `"stale"` otherwise (including when `None`)
- Return HTTP 503 when `ssh_cache` is stale (consistent with existing unhealthy behaviour)
- Update `docs/architecture.md`

## Out of scope

- Separate TTL for SSH cache staleness threshold (reuses `SYNC_INTERVAL_SECONDS * 1.1`)
- Any changes to the sync pipeline or background task

## Proposed approach

In `health()` in `main.py`, after computing `now`, add:

```python
ssh_stale = (
    last_updated_timestamp is None
    or now - last_updated_timestamp > timedelta(seconds=SYNC_INTERVAL_SECONDS * 1.1)
)
checks["ssh_cache"] = "stale" if ssh_stale else "ok"
```

## Acceptance criteria

- [ ] `/health` response includes `"ssh_cache": "ok"` when cache is fresh
- [ ] `/health` returns `"ssh_cache": "stale"` and HTTP 503 when cache is `None` or too old
- [ ] Existing healthy-path tests updated to include the new key
- [ ] All existing tests pass

## Related tickets

- `005-ssh-cache-stale-check.md` — introduced the `SYNC_INTERVAL_SECONDS * 1.1` staleness threshold for SSH logins
