# Dry-run mode

**Status:** done

## Goal

Add a `DRY_RUN` environment variable that makes janus show what it *would* do without actually modifying Outline. In dry-run mode only the startup sync runs (no periodic background syncs), all Outline write operations are skipped, and the SSH validation endpoint returns no key. This lets operators safely preview the effect of running janus against a live instance before committing.

## Background

All Outline mutations live in four low-level functions in `outline.py` (`create_outline_group`, `delete_outline_group`, `add_group_membership`, `delete_group_membership`) plus two inlined `_post` calls for suspend/activate in `sync_suspended_status`. The read path (fetching users and groups from PocketID and Outline) is unchanged — dry-run still fetches real data to produce an accurate preview.

The sync is triggered from `main.py`'s lifespan and `_scheduled_sync`. SSH validation is in `GET /ssh/validate`.

## Scope

- `DRY_RUN` env var (truthy values: `1`, `true`, `yes`, case-insensitive)
- `outline.py`: skip all write API calls when `DRY_RUN` is set; per-operation logs still fire so the operator sees what would have changed; summary logs append `(dry run)`
- `main.py`: log a prominent warning at startup when `DRY_RUN` is set; run the startup sync once but do not start `_scheduled_sync`; `GET /ssh/validate` returns HTTP 204 immediately without validation

## Out of scope

- Dry-run mode for PocketID (read-only by design already)
- Any UI or report output beyond the existing log lines

## Proposed approach

**`outline.py`**

Add at module level (after env var reads):
```python
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
```

Gate each mutating function:
```python
def create_outline_group(name: str) -> dict:
    if DRY_RUN:
        return {}
    ...

def delete_outline_group(group_id: str) -> None:
    if DRY_RUN:
        return
    ...

def add_group_membership(group_id: str, user_id: str) -> None:
    if DRY_RUN:
        return
    ...

def delete_group_membership(group_id: str, user_id: str) -> None:
    if DRY_RUN:
        return
    ...
```

In `sync_suspended_status`, guard both `_post` calls:
```python
if pocket_user.disabled and not outline_user.suspended:
    logger.info("Suspending user %s", outline_user.email)
    if not DRY_RUN:
        _post("users.suspend", {"id": outline_user.id})
    suspended += 1
elif not pocket_user.disabled and outline_user.suspended:
    logger.info("Reactivating user %s", outline_user.email)
    if not DRY_RUN:
        _post("users.activate", {"id": outline_user.id})
    reactivated += 1
```

Append `" (dry run)"` to the four summary log messages when `DRY_RUN` is set.

**`main.py`**

Add at module level:
```python
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
```

In `lifespan`, after the startup sync, only start the background task when not in dry-run mode:
```python
if DRY_RUN:
    logger.warning("DRY RUN mode enabled — no Outline changes will be made, no periodic syncs will run")
    yield
else:
    task = asyncio.create_task(_scheduled_sync(last_force_sync=startup_time))
    yield
    task.cancel()
    ...
```

In `GET /ssh/validate`:
```python
if DRY_RUN:
    return PlainTextResponse("", status_code=204)
```

## Acceptance criteria

- [ ] With `DRY_RUN` unset or falsy, behaviour is identical to current
- [ ] With `DRY_RUN=true`, no Outline API write calls are made (groups.create, groups.delete, groups.add_user, groups.remove_user, users.suspend, users.activate)
- [ ] Outline read calls still happen (groups.list, users.list, groups.memberships) so the preview reflects real data
- [ ] Per-operation log lines still fire in dry-run ("Creating group X", "Adding alice to Eng", etc.)
- [ ] Summary log lines append `(dry run)` in dry-run mode
- [ ] A prominent WARNING is logged at startup when `DRY_RUN` is set
- [ ] Only the startup sync runs in dry-run mode — no `_scheduled_sync` task is started
- [ ] `GET /ssh/validate` returns HTTP 204 immediately in dry-run mode
- [ ] All existing tests pass; new tests cover dry-run behaviour for each mutating function, the lifespan, and the SSH endpoint

## Related tickets

- `003-improve-sync-logging.md` — the summary log lines this ticket appends `(dry run)` to
