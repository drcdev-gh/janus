from pocket import PocketUser
from ssh import validate_keyformat, validate_pubkey

# SSH_ALLOWED_GROUP is "SSH Users" as set in conftest.py

VALID_ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
VALID_RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCrV6fquDFAkjK"
VALID_ECDSA_256 = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAI"
VALID_ECDSA_384 = "ecdsa-sha2-nistp384 AAAAE2VjZHNhLXNoYTItbmlzdHAzODQAAAAI"
VALID_ECDSA_521 = "ecdsa-sha2-nistp521 AAAAE2VjZHNhLXNoYTItbmlzdHA1MjEAAAAI"


def _user(email, groups=None, claims=None):
    return PocketUser(
        username="alice",
        user_id="u1",
        email=email,
        groups=groups or [],
        custom_claims=claims or [],
        disabled=False,
    )


# ---------------------------------------------------------------------------
# validate_keyformat
# ---------------------------------------------------------------------------

class TestValidateKeyFormat:
    def test_valid_ed25519(self):
        assert validate_keyformat(VALID_ED25519) is True

    def test_valid_rsa(self):
        assert validate_keyformat(VALID_RSA) is True

    def test_valid_ecdsa_nistp256(self):
        assert validate_keyformat(VALID_ECDSA_256) is True

    def test_valid_ecdsa_nistp384(self):
        assert validate_keyformat(VALID_ECDSA_384) is True

    def test_valid_ecdsa_nistp521(self):
        assert validate_keyformat(VALID_ECDSA_521) is True

    def test_valid_key_with_comment(self):
        assert validate_keyformat(f"{VALID_ED25519} alice@laptop") is True

    def test_valid_key_with_leading_and_trailing_whitespace(self):
        assert validate_keyformat(f"  {VALID_ED25519}  ") is True

    def test_invalid_empty_string(self):
        assert validate_keyformat("") is False

    def test_invalid_unsupported_type(self):
        assert validate_keyformat("ssh-dss AAAA") is False

    def test_invalid_missing_key_data(self):
        assert validate_keyformat("ssh-ed25519") is False

    def test_invalid_bad_characters_in_key(self):
        assert validate_keyformat("ssh-ed25519 !!!INVALID!!!") is False

    def test_invalid_unsupported_ecdsa_curve(self):
        assert validate_keyformat("ecdsa-sha2-nistp224 AAAA") is False


# ---------------------------------------------------------------------------
# validate_pubkey
# ---------------------------------------------------------------------------

class TestValidatePubkey:
    def test_matching_key_returns_key_string(self):
        user = _user("alice@example.com", ["SSH Users"],
                     [{"key": "ssh-pubkey", "value": VALID_ED25519}])
        assert validate_pubkey(VALID_ED25519, [user]) == VALID_ED25519

    def test_wrong_key_returns_none(self):
        user = _user("alice@example.com", ["SSH Users"],
                     [{"key": "ssh-pubkey", "value": VALID_ED25519}])
        assert validate_pubkey("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDIFFERENT", [user]) is None

    def test_user_not_in_allowed_group_returns_none(self):
        user = _user("alice@example.com", ["Other Group"],
                     [{"key": "ssh-pubkey", "value": VALID_ED25519}])
        assert validate_pubkey(VALID_ED25519, [user]) is None

    def test_invalid_key_format_returns_none(self):
        assert validate_pubkey("not-a-valid-key", []) is None

    def test_empty_user_store_returns_none(self):
        assert validate_pubkey(VALID_ED25519, []) is None

    def test_none_user_store_returns_none(self):
        assert validate_pubkey(VALID_ED25519, None) is None

    def test_no_ssh_claim_returns_none(self):
        user = _user("alice@example.com", ["SSH Users"],
                     [{"key": "other-claim", "value": "ignored"}])
        assert validate_pubkey(VALID_ED25519, [user]) is None

    def test_first_matching_user_wins(self):
        user1 = _user("alice@example.com", ["SSH Users"],
                      [{"key": "ssh-pubkey", "value": VALID_ED25519}])
        user2 = _user("bob@example.com", ["SSH Users"],
                      [{"key": "ssh-pubkey", "value": VALID_ED25519}])
        assert validate_pubkey(VALID_ED25519, [user1, user2]) == VALID_ED25519
