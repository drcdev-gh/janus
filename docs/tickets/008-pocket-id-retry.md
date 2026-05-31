# Retry transient PocketID request failures

**Status:** done

## Goal

A single network hiccup or momentary PocketID 5xx fails the entire sync and poisons `last_sync_error`, potentially making janus appear unhealthy and blocking SSH logins. Add a simple retry with backoff to `pocket._paginate` so transient errors are absorbed without surfacing to callers.

## Background

`pocket._paginate` makes one `requests.get` per page with a 2-second timeout. Any `ConnectionError`, `Timeout`, or 5xx response raises immediately, propagating up through `sync_from_pocket_id` → `update_pocket_userstore` → `_run_sync`, where it's caught and stored as `last_sync_error`. Three total attempts (two retries) with 1 s and 3 s backoff is enough to survive transient blips without making syncs unreasonably slow on persistent failures.

## Scope

- 3 total attempts per page request (2 retries) with 1 s then 3 s backoff
- Retry on: `requests.ConnectionError`, `requests.Timeout`, HTTP 5xx
- Do not retry on HTTP 4xx (deterministic auth/not-found errors)
- Log a WARNING before each retry with attempt number and error
- No new environment variables — retry count and delays are hardcoded

## Out of scope

- Per-attempt timeout tuning
- Retry configuration via env vars
- Retrying at the sync level (caller-level retry)

## Proposed approach

In `pocket.py`, add `import time` and a module-level `_RETRY_DELAYS = (1, 3)`. In `_paginate`, wrap the `requests.get` call:

```python
for i, backoff in enumerate([0] + list(_RETRY_DELAYS)):
    if backoff:
        logger.warning("PocketID request failed, retrying in %ds (attempt %d/3): %s", backoff, i + 1, exc)
        time.sleep(backoff)
    try:
        resp = requests.get(...)
        if resp.status_code >= 500:
            exc = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            continue
        resp.raise_for_status()   # raises for 4xx — not retried
        exc = None
        break
    except (requests.ConnectionError, requests.Timeout) as e:
        exc = e
if exc is not None:
    raise exc
```

## Acceptance criteria

- [ ] `ConnectionError` and `Timeout` are retried up to 2 times with 1 s / 3 s backoff
- [ ] HTTP 5xx is retried; HTTP 4xx raises immediately without retry
- [ ] A WARNING is logged before each retry
- [ ] After all retries exhausted, the original exception is re-raised
- [ ] Tests patch `time.sleep` to avoid real delays; all existing tests pass

## Related tickets

- `002-health-check-endpoint.md` — `last_sync_error` is what the health check surfaces
