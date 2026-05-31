import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from datetime import datetime, timedelta, timezone
import hmac
import logging
import socket
import sys
import os
from urllib.parse import urlparse

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
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

# Quick caching mechanism for the PocketID user store.
# TODO: not thread-safe — use a threading.Lock or cachetools.TTLCache if running
# with multiple Uvicorn workers.
pocket_userstore = None
last_updated_timestamp = None

SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", 30 * 60))
FORCE_SYNC_INTERVAL_SECONDS = int(os.getenv("FORCE_SYNC_INTERVAL_SECONDS", 3 * 60 * 60))
HEALTH_CACHE_SECONDS = 60

last_sync_error: str | None = None
_health_cache: dict | None = None
_health_cache_time: datetime | None = None


def _ping_host(url: str, timeout: float = 3.0) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    with socket.create_connection((host, port), timeout=timeout):
        pass


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


def _run_sync(force: bool = False) -> str | None:
    """Run the full Outline sync.

    When force=False, skips the Outline sync if PocketID data is unchanged.
    Returns None on success, or an error message if the sync was skipped
    due to an empty PocketID store or group list. Raises on API errors.
    """
    changed = update_pocket_userstore(True)

    if not pocket_userstore:
        return "empty Pocket user store"

    if not force and not changed:
        logger.info("PocketID data unchanged, skipping Outline sync")
        return None

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


def _is_force_due(last_force: datetime, now: datetime) -> bool:
    return (now - last_force) >= timedelta(seconds=FORCE_SYNC_INTERVAL_SECONDS)


async def _scheduled_sync(last_force_sync: datetime):
    """Background task: normal sync every SYNC_INTERVAL_SECONDS, force sync every FORCE_SYNC_INTERVAL_SECONDS.

    Tracks elapsed time to decide which to run each tick, so force and normal
    syncs never fire simultaneously. _run_sync makes blocking HTTP calls so it
    runs in a thread-pool executor to avoid stalling the event loop.
    """
    last_force = last_force_sync
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        force = _is_force_due(last_force, now)
        if force:
            last_force = now
        label = "force" if force else "normal"
        logger.info("Scheduled %s sync starting", label)
        global last_sync_error
        try:
            error = await asyncio.get_running_loop().run_in_executor(
                None, lambda f=force: _run_sync(force=f)
            )
            if error:
                logger.warning("Scheduled %s sync skipped: %s", label, error)
                last_sync_error = error
            else:
                logger.info("Scheduled %s sync complete", label)
                last_sync_error = None
        except Exception as e:
            logger.exception("Scheduled %s sync failed", label)
            last_sync_error = str(e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_time = datetime.now(timezone.utc)
    logger.info("Startup sync starting")
    global last_sync_error
    try:
        error = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _run_sync(force=True)
        )
        if error:
            logger.warning("Startup sync skipped: %s", error)
            last_sync_error = error
        else:
            logger.info("Startup sync complete")
            last_sync_error = None
    except Exception as e:
        logger.exception("Startup sync failed")
        last_sync_error = str(e)

    if DRY_RUN:
        logger.warning("DRY RUN mode enabled — no Outline changes will be made, no periodic syncs will run")
        yield
    else:
        task = asyncio.create_task(_scheduled_sync(last_force_sync=startup_time))
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

    result = {
        "status": "healthy" if all(v == "ok" for v in checks.values()) else "unhealthy",
        "checks": checks,
    }
    _health_cache, _health_cache_time = result, now
    return JSONResponse(result, status_code=200 if result["status"] == "healthy" else 503)


@app.post("/outline/force-sync")
def force_sync_outline(x_api_key: str = Header(...)):
    if not hmac.compare_digest(x_api_key, API_KEY):
        logger.warning("Invalid API key received")
        raise HTTPException(status_code=403)

    logger.info("Manual force sync triggered")
    error = _run_sync(force=True)
    if error:
        logger.warning("Force sync skipped: %s", error)
        raise HTTPException(status_code=404)
    return {"status": "ok"}


@app.get("/ssh/validate")
def validate_ssh_login(pubkey: str = Query(max_length=8192), x_api_key: str = Header(...)):
    if not hmac.compare_digest(x_api_key, API_KEY):
        logger.warning("Invalid API key received")
        raise HTTPException(status_code=403)

    if DRY_RUN:
        return PlainTextResponse("", status_code=204)

    update_pocket_userstore(False)
    key = ssh.validate_pubkey(pubkey, pocket_userstore)
    if key is None:
        return PlainTextResponse("", status_code=204)
    return PlainTextResponse(key + "\n")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8085)
