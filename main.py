import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from datetime import datetime, timedelta, timezone
import hmac
import logging
import sys
import os

import pocket
import outline
import ssh

logger = logging.getLogger("uvicorn")

REQUIRED_ENVS = [
    "POCKETID_API_URL",
    "POCKETID_API_KEY",
    "OUTLINE_API_URL",
    "OUTLINE_API_KEY",
    "SSH_ALLOWED_GROUP",
    "API_KEY",
]

for var in REQUIRED_ENVS:
    if var not in os.environ:
        logger.error("Required environment variable %s not set", var)
        sys.exit(1)

for url_var in ("POCKETID_API_URL", "OUTLINE_API_URL"):
    if not os.environ[url_var].startswith("https://"):
        logger.error("%s must use HTTPS", url_var)
        sys.exit(1)

API_KEY = os.getenv("API_KEY")

# Quick caching mechanism for the PocketID user store.
# TODO: not thread-safe — use a threading.Lock or cachetools.TTLCache if running
# with multiple Uvicorn workers.
pocket_userstore = None
last_updated_timestamp = None

SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 30 * 60))


def update_pocket_userstore(force_update: bool) -> bool:
    """Refresh the PocketID user store if stale or forced.

    Returns True if the store contents changed, False if unchanged.
    """
    global pocket_userstore, last_updated_timestamp

    has_changed = False
    now = datetime.now(timezone.utc)

    def should_refresh():
        return (
            pocket_userstore is None
            or last_updated_timestamp is None
            or now - last_updated_timestamp > timedelta(seconds=SYNC_INTERVAL_SECONDS)
        )

    if force_update or should_refresh():
        new_userstore = pocket.sync_from_pocket_id()

        if new_userstore != pocket_userstore:
            has_changed = True

        pocket_userstore = new_userstore
        last_updated_timestamp = now

    return has_changed


def _run_sync() -> str | None:
    """Run the full Outline sync.

    Returns None on success, or an error message if the sync was skipped
    due to an empty PocketID store or group list. Raises on API errors.
    """
    update_pocket_userstore(True)

    if not pocket_userstore:
        return "empty Pocket user store"

    pocket_groups = pocket.get_unique_groups()

    if not pocket_groups:
        return "empty Pocket groups"

    # Fetch Outline groups once; create_missing_groups / delete_extra_groups
    # return an updated list so we never need to re-fetch from the API.
    outline_groups = outline.fetch_outline_groups()
    outline_groups = outline.create_missing_groups(pocket_groups, outline_groups)
    outline_groups = outline.delete_extra_groups(pocket_groups, outline_groups)

    group_name_to_id = outline.build_group_name_to_id(outline_groups)

    # Build the Outline user store once and share it across all sync operations.
    outline_users = outline.build_outline_user_store(outline_groups)

    outline.sync_group_memberships(pocket_userstore, outline_users, group_name_to_id)
    outline.sync_suspended_status(pocket_userstore, outline_users)
    return None


async def _scheduled_sync():
    """Background task: run _run_sync every SYNC_INTERVAL_SECONDS.

    _run_sync makes blocking HTTP calls so it runs in a thread-pool executor
    to avoid stalling the event loop while SSH validations are in flight.
    """
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        logger.info("Scheduled sync starting")
        try:
            error = await asyncio.get_running_loop().run_in_executor(None, _run_sync)
            if error:
                logger.warning("Scheduled sync skipped: %s", error)
            else:
                logger.info("Scheduled sync complete")
        except Exception:
            logger.exception("Scheduled sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_scheduled_sync())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)

origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"]
)


@app.post("/outline/sync")
def sync_outline(x_api_key: str = Header(...)):
    if not hmac.compare_digest(x_api_key, API_KEY):
        logger.warning("Invalid API key received")
        raise HTTPException(status_code=403)

    logger.info("Manual sync triggered")
    error = _run_sync()
    if error:
        logger.warning("Sync skipped: %s", error)
        raise HTTPException(status_code=404)
    return {"status": "ok"}


@app.get("/ssh/validate")
def validate_ssh_login(pubkey: str = Query(max_length=8192), x_api_key: str = Header(...)):
    if not hmac.compare_digest(x_api_key, API_KEY):
        logger.warning("Invalid API key received")
        raise HTTPException(status_code=403)

    update_pocket_userstore(False)
    key = ssh.validate_pubkey(pubkey, pocket_userstore)
    if key is None:
        return PlainTextResponse("", status_code=204)
    return PlainTextResponse(key + "\n")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8085)
