# Tiered sync scheduling and force-sync API

**Status:** done

## Goal

Improve sync reliability by running a force sync at startup, adding a `/outline/force-sync` API endpoint for on-demand full syncs, and introducing a tiered background loop: normal sync every 30 minutes (skips Outline API calls if PocketID data is unchanged), escalating to a force sync whenever 3 hours have elapsed since the last one. This ensures new Outline users get reconciled promptly even if nothing changed in PocketID.

## Background

Currently `_run_sync()` always fetches fresh PocketID data and always runs the full Outline sync pipeline. No sync runs at startup. The single background task runs every `SYNC_INTERVAL_SECONDS` (default 30 min).

`update_pocket_userstore(force_update)` already returns a boolean indicating whether the PocketID store changed; this can gate the Outline sync in the normal path.

## Scope

- Run a force sync in the FastAPI `lifespan` before yielding (startup)
- Add `force: bool` parameter to `_run_sync`; when `force=False` and PocketID data is unchanged, log and return early without touching the Outline API
- Replace the two-task approach with a single background task that tracks elapsed time since the last force sync and decides per iteration whether to run force or normal
- Add `POST /outline/force-sync` route (authenticated with `x-api-key`); remove `POST /outline/sync`
- Make the force-sync interval configurable via `FORCE_SYNC_INTERVAL_SECONDS` (default `3 * 60 * 60`)

## Out of scope

- Rate limiting the API endpoints
- Any changes to PocketID or Outline API clients (`pocket.py`, `outline.py`)
- SSH validation changes

## Proposed approach

**`_run_sync(force: bool = False)`**
After `update_pocket_userstore(True)` returns `changed`, insert before `get_unique_groups()`:
```python
if not force and not changed:
    logger.info("PocketID data unchanged, skipping Outline sync")
    return None
```

**`_scheduled_sync(last_force_sync: datetime)`**
Single task, wakes every `SYNC_INTERVAL_SECONDS`, decides on force vs normal by elapsed time:
```python
async def _scheduled_sync(last_force_sync: datetime):
    last_force = last_force_sync
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        force = (now - last_force) >= timedelta(seconds=FORCE_SYNC_INTERVAL_SECONDS)
        if force:
            last_force = now
        try:
            error = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _run_sync(force=force)
            )
            ...
```

**`lifespan`**
Records startup time, passes it to the background task so the first 3-hour force sync is deferred by the full interval (startup already covered it):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_time = datetime.now(timezone.utc)
    loop = asyncio.get_running_loop()
    try:
        err = await loop.run_in_executor(None, lambda: _run_sync(force=True))
        ...
    except Exception:
        logger.exception("Startup sync failed")

    task = asyncio.create_task(_scheduled_sync(last_force_sync=startup_time))
    yield
    task.cancel()
    ...
```

**Routes**
Remove `POST /outline/sync`. Add `POST /outline/force-sync` calling `_run_sync(force=True)`.

**Env var**
```python
FORCE_SYNC_INTERVAL_SECONDS = int(os.getenv("FORCE_SYNC_INTERVAL_SECONDS", 3 * 60 * 60))
```

## Acceptance criteria

- [ ] A force sync runs at app startup before the first request is served
- [ ] `POST /outline/force-sync` triggers a full sync and returns `{"status": "ok"}`
- [ ] Wrong or missing API key on `/outline/force-sync` returns 403
- [ ] `POST /outline/sync` is removed (returns 404)
- [ ] Background normal sync (30 min) skips Outline API calls when PocketID data is unchanged
- [ ] Background force sync fires when ≥ 3 hours have elapsed since the last force sync, replacing that tick's normal sync — not in addition to it
- [ ] The first scheduled force sync fires ~3 hours after startup (startup sync counts as the last force)
- [ ] `FORCE_SYNC_INTERVAL_SECONDS` env var controls the force-sync interval
- [ ] Startup sync failure logs an error but does not prevent the app from starting
- [ ] All existing tests pass; new tests cover the `force=False` skip path, the force-vs-normal decision logic, and the new route
