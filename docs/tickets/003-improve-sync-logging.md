# Improve sync logging for traceability

**Status:** done

## Goal

Add fetch-count and summary logs to `pocket.py` and `outline.py` so an operator can see at a glance how many users and groups were fetched from each system, and how many permissions were actually changed during each sync — without having to count individual log lines.

## Background

The current log for a sync looks roughly like:
```
INFO: Startup sync starting
INFO: Adding alice@example.com to group Engineering
INFO: Suspending user bob@example.com
INFO: Startup sync complete
```

There is no visibility into how many users/groups were fetched, and no summary of total changes per sync phase. `pocket.py` has no logger at all.

All modules currently use `logging.getLogger("uvicorn")` — new logging in `pocket.py` should follow the same pattern.

## Scope

- **`pocket.py`**: add a logger; log fetch counts after `sync_from_pocket_id` and `get_unique_groups`
- **`outline.py`**: log fetch count in `build_outline_user_store`; add per-function summary counts to `create_missing_groups`, `delete_extra_groups`, `sync_group_memberships`, and `sync_suspended_status`

## Out of scope

- Changes to `main.py` or `ssh.py`
- Structured/JSON logging or log-level configuration
- Propagating counts up to `_run_sync` for a single top-level summary line

## Proposed approach

**`pocket.py`**

Add `logger = logging.getLogger("uvicorn")` at module level.

```python
def sync_from_pocket_id() -> list[PocketUser]:
    users = [...]
    logger.info("Fetched %d users from PocketID", len(users))
    return users

def get_unique_groups() -> set[str]:
    groups = {g["name"] for g in _paginate("user-groups")}
    logger.info("Found %d groups in PocketID", len(groups))
    return groups
```

**`outline.py`**

`build_outline_user_store` — log after building the store:
```python
logger.info("Fetched %d users from Outline across %d groups", len(store), len(groups))
```

`create_missing_groups` — track and log created count:
```python
created = 0
for name in pocket_groups:
    if name not in existing:
        ...
        created += 1
logger.info("Groups: %d created", created)
```

`delete_extra_groups` — track and log deleted count:
```python
deleted = 0
for group in groups:
    if group["name"] not in pocket_groups:
        ...
        deleted += 1
logger.info("Groups: %d deleted", deleted)
```

`sync_group_memberships` — add counters alongside existing per-operation logs:
```python
added = removed = 0
...
add_group_membership(...); added += 1
...
delete_group_membership(...); removed += 1
logger.info("Group memberships: %d added, %d removed", added, removed)
```

`sync_suspended_status` — same pattern:
```python
suspended = reactivated = 0
...
suspended += 1 / reactivated += 1
logger.info("User status: %d suspended, %d reactivated", suspended, reactivated)
```

## Acceptance criteria

- [ ] After `sync_from_pocket_id`, a log line records how many users were fetched from PocketID
- [ ] After `get_unique_groups`, a log line records how many groups were found in PocketID
- [ ] After `build_outline_user_store`, a log line records how many users and groups were fetched from Outline
- [ ] After each call to `create_missing_groups`, a summary log records how many groups were created (including zero)
- [ ] After each call to `delete_extra_groups`, a summary log records how many groups were deleted (including zero)
- [ ] After `sync_group_memberships`, a summary log records total memberships added and removed
- [ ] After `sync_suspended_status`, a summary log records total users suspended and reactivated
- [ ] All existing tests pass; new tests verify the summary log messages
