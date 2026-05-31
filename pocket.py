import logging
import os
import requests
from dataclasses import dataclass

logger = logging.getLogger("uvicorn")

BASE_URL = os.getenv("POCKETID_API_URL")
API_KEY = os.getenv("POCKETID_API_KEY")

if not BASE_URL or not API_KEY:
    raise RuntimeError("POCKETID_API_URL and POCKETID_API_KEY must be set")

HEADERS = {
    "X-API-KEY": API_KEY,
    "Accept": "application/json",
}


@dataclass
class PocketUser:
    username: str
    user_id: str
    email: str
    groups: list[str]
    custom_claims: list[dict]
    disabled: bool


def _paginate(path: str) -> list[dict]:
    all_items = []
    page = 1
    limit = 50
    while True:
        resp = requests.get(
            f"{BASE_URL}/api/{path}",
            headers=HEADERS,
            params={"pagination[page]": page, "pagination[limit]": limit},
            timeout=2,
        )
        resp.raise_for_status()
        body = resp.json()
        items = body["data"]
        last_page = body["pagination"]["totalPages"]
        if not items:
            break
        all_items.extend(items)
        if last_page == page:
            break
        page += 1
    return all_items


def sync_from_pocket_id() -> list[PocketUser]:
    users = [
        PocketUser(
            username=u["username"],
            user_id=u["id"],
            email=u["email"],
            groups=[g["name"] for g in u["userGroups"]],
            custom_claims=u["customClaims"],
            disabled=u["disabled"],
        )
        for u in _paginate("users")
    ]
    logger.info("Fetched %d users from PocketID", len(users))
    return users


def get_unique_groups() -> set[str]:
    """Return the authoritative group set directly from PocketID.

    Using /api/user-groups is more correct than deriving groups from user records
    because it includes groups that exist but currently have no members.
    """
    groups = {g["name"] for g in _paginate("user-groups")}
    logger.info("Found %d groups in PocketID", len(groups))
    return groups
