# Guard Outline writes against suspicious PocketID data

**Status:** done

## Goal

Prevent erroneous changes from being written to Outline when PocketID data looks wrong. Before **any** Outline write operation, verify that the PocketID cache is fresh, that PocketID returned non-empty users and groups, and that Outline does not have more users than PocketID. Any failed check aborts the entire sync with a warning log.

## Background

`_run_sync` in `main.py` currently has two early-return guards (empty user store, empty group list) that already prevent Outline writes. However:

- The guards log via the caller only, not at the point of detection
- There is no check for Outline user count vs PocketID user count — if PocketID silently returns fewer users than expected, Outline could have memberships removed or users suspended incorrectly
- There is no explicit freshness guard on the cache before writes begin

The current pipeline order makes the user count check impossible before writes because `build_outline_user_store` runs after `create_missing_groups` / `delete_extra_groups`. The fix is to move `build_outline_user_store` earlier — before any writes — and reuse that result for the rest of the pipeline. This is safe because:
- Newly created groups start empty, so nothing is missed
- Deleted groups are excluded via `group_name_to_id`, so stale membership data for them is harmless

See `docs/architecture.md` — Outline sync pipeline section.

## Scope

- Add explicit `logger.warning` in `_run_sync` at the point each existing guard fires (empty users, empty groups), not only in the caller
- Add cache freshness guard before any Outline write: abort if `last_updated_timestamp` is `None` or older than `SYNC_INTERVAL_SECONDS * 1.1`
- Move `build_outline_user_store` call to before `create_missing_groups` / `delete_extra_groups`
- After `build_outline_user_store`, before any write: check `len(outline_users) > len(pocket_userstore)` → log warning with counts, return error string

## Out of scope

- Making the user count threshold configurable
- Fixing `/outline/force-sync` returning HTTP 500 on unhandled sync exceptions (separate issue)

## Proposed approach

Restructure `_run_sync` so all reads and safety checks happen before any writes:

```python
# --- PocketID-side checks (before touching Outline) ---
with _userstore_lock:
    ts = last_updated_timestamp
if ts is None or datetime.now(timezone.utc) - ts > timedelta(seconds=SYNC_INTERVAL_SECONDS * 1.1):
    logger.warning("Outline sync aborted: PocketID cache is stale or empty")
    return "stale PocketID cache"

if not pocket_userstore:
    logger.warning("Outline sync aborted: PocketID returned empty user list")
    return "empty Pocket user store"

# ... existing force/changed check ...

pocket_groups = pocket.get_unique_groups()
if not pocket_groups:
    logger.warning("Outline sync aborted: PocketID returned empty group list")
    return "empty Pocket groups"

# --- Outline reads + user count check (still before any write) ---
outline_groups = outline.fetch_outline_groups()
outline_users = outline.build_outline_user_store(outline_groups)  # moved earlier

if len(outline_users) > len(pocket_userstore):
    logger.warning(
        "Outline sync aborted: Outline has %d users but PocketID only returned %d — "
        "possible incomplete PocketID fetch",
        len(outline_users), len(pocket_userstore),
    )
    return "Outline user count exceeds PocketID user count"

# --- Writes ---
outline_groups = outline.create_missing_groups(pocket_groups, outline_groups)
outline_groups = outline.delete_extra_groups(pocket_groups, outline_groups)
group_name_to_id = outline.build_group_name_to_id(outline_groups)

outline.sync_group_memberships(pocket_userstore, outline_users, group_name_to_id)
outline.sync_suspended_status(pocket_userstore, outline_users)
```

## Acceptance criteria

- [ ] Warning logged in `_run_sync` at the detection point for each guard (stale cache, empty users, empty groups, Outline user count)
- [ ] All four guards fire before any Outline write call
- [ ] `build_outline_user_store` is called once, before group creates/deletes, and its result is reused for membership/suspend sync
- [ ] All abort conditions return an error string so callers set `last_sync_error` and `/health` reflects the problem
- [ ] Tests cover each new guard; existing sync pipeline tests still pass
- [ ] All existing tests pass

## Related tickets

- `005-ssh-cache-stale-check.md` — established `SYNC_INTERVAL_SECONDS * 1.1` as the staleness threshold
- `006-health-ssh-cache-check.md` — `last_sync_error` surfaces in `/health`
- `007-userstore-thread-safety.md` — `_userstore_lock` must be used when reading `last_updated_timestamp`
