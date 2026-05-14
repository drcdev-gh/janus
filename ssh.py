import logging
import os
import re

logger = logging.getLogger("uvicorn")

SSH_ALLOWED_GROUP = os.environ["SSH_ALLOWED_GROUP"]

PUBKEY_RE = re.compile(
    r"^(ssh-(rsa|ed25519)|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+(?: .*)?$"
)


def validate_keyformat(pubkey: str) -> bool:
    return bool(PUBKEY_RE.fullmatch(pubkey.strip()))


def validate_pubkey(pubkey: str, users) -> str | None:
    """Return the validated public key string on success, None on failure."""
    if not validate_keyformat(pubkey):
        logger.warning("Invalid Public Key Format: %s", pubkey)
        return None

    if not users:
        logger.warning("Unable to fetch users")
        return None

    for user in users:
        for group in user.groups:
            if SSH_ALLOWED_GROUP == group:
                for custom_claim in user.custom_claims:
                    if custom_claim["key"] == "ssh-pubkey":
                        if custom_claim["value"] == pubkey:
                            logger.info("Authorizing login for %s with key %s",
                                        user.username, pubkey)
                            return custom_claim["value"]
                break

    logger.warning("No matching Public Key found: %s", pubkey)
    return None
