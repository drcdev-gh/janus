# Thread-safe pocket_userstore access

**Status:** done

## Goal

`pocket_userstore` and `last_updated_timestamp` are module-level globals written by `update_pocket_userstore` (called from a thread-pool executor) and read by sync route handlers (also in thread-pool threads). Without a lock, a torn read is possible: a handler could observe a fresh timestamp alongside an old store, or vice versa. Add a `threading.Lock` to make all reads and writes atomic.

## Background

`docs/architecture.md` explicitly notes: "the cache is not thread-safe. Safe with the default single Uvicorn worker; requires a lock if running with multiple workers." Even with a single Uvicorn worker, `run_in_executor` runs `_run_sync` in a thread-pool thread concurrently with FastAPI's own thread-pool threads for sync route handlers, so the risk exists today.

## Scope

- Add `_userstore_lock = threading.Lock()` in `main.py`
- `update_pocket_userstore`: snapshot current values under lock, do HTTP outside lock, write result under lock
- `validate_ssh_login`: snapshot `last_updated_timestamp` and `pocket_userstore` under lock before the staleness check
- `health()`: snapshot `last_updated_timestamp` under lock before the `ssh_cache` check
- Remove the thread-safety caveat from `docs/architecture.md`

## Out of scope

- Switching to `cachetools.TTLCache` or other cache libraries
- Multi-worker (Gunicorn) support — single Uvicorn worker remains the supported deployment

## Proposed approach

```python
import threading
_userstore_lock = threading.Lock()
```

`update_pocket_userstore`: read globals under lock, call `pocket.sync_from_pocket_id()` outside (slow HTTP must not hold the lock), then write results under lock.

Route handlers: acquire lock only long enough to snapshot both `last_updated_timestamp` and `pocket_userstore` into locals, then release before any business logic.

## Acceptance criteria

- [ ] `_userstore_lock` is a `threading.Lock` acquired around all reads and writes of `pocket_userstore` / `last_updated_timestamp`
- [ ] `pocket.sync_from_pocket_id()` is never called while the lock is held
- [ ] All existing tests pass without modification

## Related tickets

- `005-ssh-cache-stale-check.md` — reads `last_updated_timestamp` in `validate_ssh_login`
- `006-health-ssh-cache-check.md` — reads `last_updated_timestamp` in `health()`
