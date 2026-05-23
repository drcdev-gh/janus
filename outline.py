import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

import requests

from pocket import PocketUser

logger = logging.getLogger("uvicorn")

BASE_URL = os.getenv("OUTLINE_API_URL")

# Required Outline permissions:
# groups.create groups.list groups.delete groups.memberships
# groups.add_user groups.remove_user
# users.list users.suspend users.activate
API_KEY = os.getenv("OUTLINE_API_KEY")
if not BASE_URL or not API_KEY:
    raise RuntimeError("OUTLINE_API_URL and OUTLINE_API_KEY must be set")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


@dataclass
class OutlineUser:
    id: str
    name: str
    email: str | None
    groups: list[str]
    suspended: bool


def _post(endpoint: str, payload: dict | None = None) -> dict:
    resp = requests.post(
        f"{BASE_URL}/api/{endpoint}",
        json=payload or {},
        headers=HEADERS,
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def _paginate_post(
    endpoint: str,
    payload: dict,
    extract: Callable[[dict], list[dict]],
) -> list[dict]:
    all_items = []
    offset = 0
    limit = 100
    while True:
        body = _post(endpoint, {**payload, "limit": limit, "offset": offset})
        items = extract(body)
        if not items:
            break
        all_items.extend(items)
        if len(items) < limit:
            break
        offset += limit
    return all_items


def fetch_outline_users() -> list[dict]:
    return _paginate_post(
        "users.list",
        {"filter": "all"},
        lambda b: b.get("data", []),
    )


def fetch_outline_groups() -> list[dict]:
    return _paginate_post(
        "groups.list",
        {},
        lambda b: b.get("data", {}).get("groups", []),
    )


def _fetch_group_members(group_id: str) -> list[dict]:
    return _paginate_post(
        "groups.memberships",
        {"id": group_id},
        lambda b: b.get("data", {}).get("users", []),
    )


def build_group_name_to_id(groups: list[dict]) -> dict[str, str]:
    return {g["name"]: g["id"] for g in groups}


def create_outline_group(name: str) -> dict:
    body = _post("groups.create", {"name": name, "externalId": name})
    return body.get("data", {})


def delete_outline_group(group_id: str) -> None:
    _post("groups.delete", {"id": group_id})


def add_group_membership(group_id: str, user_id: str) -> None:
    _post("groups.add_user", {"id": group_id, "userId": user_id})


def delete_group_membership(group_id: str, user_id: str) -> None:
    _post("groups.remove_user", {"id": group_id, "userId": user_id})


def build_outline_user_store(groups: list[dict]) -> list[OutlineUser]:
    """Build an OutlineUser list with current group memberships.

    Accepts pre-fetched groups to avoid a redundant API call; the group list
    should already reflect any create/delete operations performed this sync.
    """
    store: dict[str, OutlineUser] = {}
    for u in fetch_outline_users():
        store[u["id"]] = OutlineUser(
            id=u["id"],
            name=u["name"],
            email=u.get("email"),
            groups=[],
            suspended=u.get("isSuspended", False),
        )

    for g in groups:
        for member in _fetch_group_members(g["id"]):
            user_id = member["id"]
            if user_id in store:
                store[user_id].groups.append(g["name"])
            else:
                logger.warning("User %s in group %s not found in user list",
                               user_id, g["name"])

    return list(store.values())


def create_missing_groups(pocket_groups: set[str], groups: list[dict]) -> list[dict]:
    """Create groups present in PocketID but absent from Outline.

    Returns an updated groups list that includes newly created entries so
    callers do not need to re-fetch from the API.
    """
    existing = {g["name"] for g in groups}
    updated = list(groups)
    for name in pocket_groups:
        if name not in existing:
            logger.info("Creating group %s", name)
            new_group = create_outline_group(name)
            if new_group:
                updated.append(new_group)
    return updated


def delete_extra_groups(pocket_groups: set[str], groups: list[dict]) -> list[dict]:
    """Delete groups present in Outline but absent from PocketID.

    Returns an updated groups list with deleted entries removed so callers
    do not need to re-fetch from the API.
    """
    remaining = []
    for group in groups:
        if group["name"] in pocket_groups:
            remaining.append(group)
        else:
            logger.info("Deleting group %s", group["name"])
            delete_outline_group(group["id"])
    return remaining


def sync_group_memberships(
    pocket_users: list[PocketUser],
    outline_users: list[OutlineUser],
    group_name_to_id: dict[str, str],
) -> None:
    """Add missing and remove extra group memberships in a single pass.

    Builds a pocket email→user map once up front so each outline user lookup
    is O(1) rather than O(n).
    """
    pocket_by_email = {u.email: u for u in pocket_users}
    for outline_user in outline_users:
        if outline_user.email is None:
            continue
        pocket_user = pocket_by_email.get(outline_user.email)
        if pocket_user is None:
            logger.warning("No PocketID user found for Outline user: %s", outline_user.email)
            continue
        if pocket_user.disabled:
            continue

        pocket_groups = set(pocket_user.groups)
        outline_groups = set(outline_user.groups)

        for group_name in pocket_groups - outline_groups:
            group_id = group_name_to_id.get(group_name)
            if group_id is None:
                logger.warning("Group %s not found in Outline", group_name)
                continue
            logger.info("Adding %s to group %s", outline_user.email, group_name)
            add_group_membership(group_id, outline_user.id)

        for group_name in outline_groups - pocket_groups:
            group_id = group_name_to_id.get(group_name)
            if group_id is None:
                logger.warning("Group %s not found in Outline", group_name)
                continue
            logger.info("Removing %s from group %s", outline_user.email, group_name)
            delete_group_membership(group_id, outline_user.id)


def sync_suspended_status(
    pocket_users: list[PocketUser],
    outline_users: list[OutlineUser],
) -> None:
    """Suspend Outline users whose PocketID account is disabled; reactivate those re-enabled."""
    pocket_by_email = {u.email: u for u in pocket_users}
    for outline_user in outline_users:
        if outline_user.email is None:
            continue
        pocket_user = pocket_by_email.get(outline_user.email)
        if pocket_user is None:
            logger.warning("Outline user %s has no matching PocketID account", outline_user.email)
            continue
        if pocket_user.disabled and not outline_user.suspended:
            logger.info("Suspending user %s", outline_user.email)
            _post("users.suspend", {"id": outline_user.id})
        elif not pocket_user.disabled and outline_user.suspended:
            logger.info("Reactivating user %s", outline_user.email)
            _post("users.activate", {"id": outline_user.id})
