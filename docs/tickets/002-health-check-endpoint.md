# Health check endpoint and Dockerfile HEALTHCHECK

**Status:** done

## Goal

Add a `GET /health` endpoint that reports whether janus is functioning correctly, and wire it into the Dockerfile as a `HEALTHCHECK` running every 5 minutes. The endpoint is unhealthy if PocketID's host is unreachable, Outline's host is unreachable, or the last sync encountered an error. Responses are cached for 60 seconds to prevent upstream host hammering if the endpoint is spammed.

## Background

janus has no automated signal for whether syncs are working. An operator only learns of broken API keys, network outages, or auth failures by noticing stale data in Outline.

The connectivity checks are TCP-level only — no API keys involved. API-level failures (wrong key, auth errors, bad responses) are already surfaced by `last_sync_error`, which tracks the outcome of the most recent sync.

The Dockerfile already has `curl` installed; the server runs on port 8085.

## Scope

- `GET /health` — unauthenticated, returns JSON with per-check detail, HTTP 200 (healthy) or 503 (unhealthy)
- Three checks: `pocketid`, `outline`, `last_sync`
- TCP reachability check for PocketID and Outline (host + port extracted from their base URLs, no HTTP request made)
- 60-second response cache (module-level, same thread-safety caveat as the existing user store cache)
- Module-level `last_sync_error: str | None` updated after every sync (startup + scheduled)
- `HEALTHCHECK` in Dockerfile: `--interval=5m --timeout=10s --retries=3`

## Out of scope

- Authentication on the health endpoint
- API-level checks for PocketID or Outline (covered by `last_sync`)
- Detailed latency or metrics reporting
- Thread-safe locking on the cache or sync-state variables

## Proposed approach

**`main.py` — TCP ping helper**
```python
import socket
from urllib.parse import urlparse

def _ping_host(url: str, timeout: float = 3.0) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    with socket.create_connection((host, port), timeout=timeout):
        pass
```

Called in the health handler as `_ping_host(os.getenv("POCKETID_API_URL"))` and `_ping_host(os.getenv("OUTLINE_API_URL"))`.

**`main.py` — new globals**
```python
last_sync_error: str | None = None
_health_cache: dict | None = None
_health_cache_time: datetime | None = None
HEALTH_CACHE_SECONDS = 60
```

**`main.py` — sync result tracking**
Update both `lifespan` and `_scheduled_sync` to set `last_sync_error` after each sync:
```python
# on success
last_sync_error = None
# on _run_sync returning an error string
last_sync_error = error
# on exception (re-raise so existing logging still fires)
except Exception as e:
    last_sync_error = str(e)
    raise
```

**`main.py` — `GET /health`**
```python
@app.get("/health")
def health():
    global _health_cache, _health_cache_time
    now = datetime.now(timezone.utc)
    if _health_cache is not None and _health_cache_time is not None:
        if (now - _health_cache_time).total_seconds() < HEALTH_CACHE_SECONDS:
            return JSONResponse(_health_cache, status_code=200 if _health_cache["status"] == "healthy" else 503)

    checks = {}
    for name, url in [("pocketid", os.getenv("POCKETID_API_URL")), ("outline", os.getenv("OUTLINE_API_URL"))]:
        try:
            _ping_host(url)
            checks[name] = "ok"
        except Exception as e:
            checks[name] = str(e)

    checks["last_sync"] = "ok" if last_sync_error is None else last_sync_error

    result = {"status": "healthy" if all(v == "ok" for v in checks.values()) else "unhealthy", "checks": checks}
    _health_cache, _health_cache_time = result, now
    return JSONResponse(result, status_code=200 if result["status"] == "healthy" else 503)
```

**`Dockerfile`**
```dockerfile
HEALTHCHECK --interval=5m --timeout=10s --retries=3 \
    CMD curl -sf http://localhost:8085/health || exit 1
```

## Acceptance criteria

- [ ] `GET /health` returns `{"status": "healthy", "checks": {"pocketid": "ok", "outline": "ok", "last_sync": "ok"}}` with HTTP 200 when all checks pass
- [ ] Returns HTTP 503 and `"status": "unhealthy"` if any check fails; the failing check shows the error string
- [ ] PocketID host unreachable (DNS failure, TCP timeout) marks `pocketid` unhealthy
- [ ] Outline host unreachable marks `outline` unhealthy
- [ ] `last_sync` is `"ok"` after a successful sync and shows the error/exception message after a failed or skipped sync
- [ ] No API keys are used in the connectivity checks
- [ ] Second request within 60 seconds returns the cached response without opening any TCP connections
- [ ] Request after 60 seconds re-runs the checks
- [ ] `HEALTHCHECK --interval=5m` present in Dockerfile
- [ ] All existing tests pass; new tests cover each unhealthy case, the healthy case, and the cache behaviour

## Related tickets

- `001-tiered-sync-scheduling.md` — introduces the sync scheduling that `last_sync_error` builds on
