import hmac
import pytest
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import main
from pocket import PocketUser

API_KEY = "test-api-key"  # matches conftest.py

client = TestClient(main.app)


def _pu(email, groups=None, disabled=False):
    return PocketUser(username="alice", user_id="u1", email=email,
                      groups=groups or [], custom_claims=[], disabled=disabled)


# ---------------------------------------------------------------------------
# Security: constant-time API key comparison
# ---------------------------------------------------------------------------

class TestApiKeyComparison:
    def test_correct_key_is_accepted(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value=set()):
            response = client.post("/outline/force-sync", headers={"x-api-key": API_KEY})
        assert response.status_code != 403

    def test_wrong_key_is_rejected(self):
        response = client.post("/outline/force-sync", headers={"x-api-key": "wrong"})
        assert response.status_code == 403

    def test_comparison_uses_compare_digest(self):
        import inspect
        source = inspect.getsource(main.force_sync_outline)
        assert "compare_digest" in source


# ---------------------------------------------------------------------------
# Security: HTTPS enforcement
# ---------------------------------------------------------------------------

class TestHttpsEnforcement:
    def test_http_pocket_url_exits(self):
        env = {"POCKETID_API_URL": "http://pocket.test",
               "POCKETID_API_KEY": "k",
               "OUTLINE_API_URL": "https://outline.test",
               "OUTLINE_API_KEY": "k",
               "SSH_ALLOWED_GROUP": "G",
               "API_KEY": "k"}
        result = subprocess.run(
            [sys.executable, "-c", "import main"],
            env=env, capture_output=True,
        )
        assert result.returncode != 0

    def test_http_outline_url_exits(self):
        env = {"POCKETID_API_URL": "https://pocket.test",
               "POCKETID_API_KEY": "k",
               "OUTLINE_API_URL": "http://outline.test",
               "OUTLINE_API_KEY": "k",
               "SSH_ALLOWED_GROUP": "G",
               "API_KEY": "k"}
        result = subprocess.run(
            [sys.executable, "-c", "import main"],
            env=env, capture_output=True,
        )
        assert result.returncode != 0

    def test_https_urls_do_not_exit(self):
        env = {"POCKETID_API_URL": "https://pocket.test",
               "POCKETID_API_KEY": "k",
               "OUTLINE_API_URL": "https://outline.test",
               "OUTLINE_API_KEY": "k",
               "SSH_ALLOWED_GROUP": "G",
               "API_KEY": "k"}
        result = subprocess.run(
            [sys.executable, "-c", "import main"],
            env=env, capture_output=True,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# update_pocket_userstore
# ---------------------------------------------------------------------------

class TestUpdatePocketUserstore:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_force_refresh_fetches_from_api(self):
        users = [_pu("a@b.com")]
        with patch("main.pocket.sync_from_pocket_id", return_value=users) as mock_sync:
            main.update_pocket_userstore(True)
        mock_sync.assert_called_once()
        assert main.pocket_userstore == users

    def test_returns_true_when_store_changed(self):
        users = [_pu("a@b.com")]
        with patch("main.pocket.sync_from_pocket_id", return_value=users):
            changed = main.update_pocket_userstore(True)
        assert changed is True

    def test_returns_false_when_store_unchanged(self):
        users = [_pu("a@b.com")]
        main.pocket_userstore = users
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main.pocket.sync_from_pocket_id", return_value=users):
            changed = main.update_pocket_userstore(True)
        assert changed is False

    def test_cache_used_when_fresh_and_not_forced(self):
        users = [_pu("a@b.com")]
        main.pocket_userstore = users
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main.pocket.sync_from_pocket_id") as mock_sync:
            main.update_pocket_userstore(False)
        mock_sync.assert_not_called()

    def test_refreshes_when_stale_even_without_force(self):
        main.pocket_userstore = []
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
        with patch("main.pocket.sync_from_pocket_id", return_value=[]) as mock_sync:
            main.update_pocket_userstore(False)
        mock_sync.assert_called_once()

    def test_refreshes_when_store_is_none(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[]) as mock_sync:
            main.update_pocket_userstore(False)
        mock_sync.assert_called_once()


# ---------------------------------------------------------------------------
# _run_sync — core behaviour
# ---------------------------------------------------------------------------

class TestRunSync:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_returns_none_on_success(self):
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}), \
             patch("main.outline.fetch_outline_groups", return_value=groups), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={}), \
             patch("main.outline.build_outline_user_store", return_value=[]), \
             patch("main.outline.sync_group_memberships"), \
             patch("main.outline.sync_suspended_status"):
            assert main._run_sync(force=True) is None

    def test_returns_error_string_when_pocket_store_empty(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[]):
            result = main._run_sync(force=True)
        assert result is not None
        assert "empty" in result.lower()

    def test_returns_error_string_when_pocket_groups_empty(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value=set()):
            result = main._run_sync(force=True)
        assert result is not None
        assert "empty" in result.lower()

    def test_propagates_api_exceptions(self):
        with patch("main.pocket.sync_from_pocket_id", side_effect=Exception("API down")):
            with pytest.raises(Exception, match="API down"):
                main._run_sync(force=True)


# ---------------------------------------------------------------------------
# _run_sync — force flag
# ---------------------------------------------------------------------------

class TestRunSyncForceFlag:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_normal_sync_skips_outline_when_pocket_unchanged(self):
        users = [_pu("a@b.com")]
        main.pocket_userstore = users
        main.last_updated_timestamp = None  # force a PocketID refresh
        with patch("main.pocket.sync_from_pocket_id", return_value=users), \
             patch("main.outline.fetch_outline_groups") as mock_outline:
            result = main._run_sync(force=False)
        assert result is None
        mock_outline.assert_not_called()

    def test_force_sync_runs_outline_even_when_pocket_unchanged(self):
        users = [_pu("a@b.com")]
        main.pocket_userstore = users
        main.last_updated_timestamp = None
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("main.pocket.sync_from_pocket_id", return_value=users), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}), \
             patch("main.outline.fetch_outline_groups", return_value=groups), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={}), \
             patch("main.outline.build_outline_user_store", return_value=[]), \
             patch("main.outline.sync_group_memberships"), \
             patch("main.outline.sync_suspended_status") as mock_suspend:
            result = main._run_sync(force=True)
        assert result is None
        mock_suspend.assert_called_once()

    def test_normal_sync_runs_outline_when_pocket_changed(self):
        main.pocket_userstore = [_pu("old@b.com")]
        main.last_updated_timestamp = None
        new_users = [_pu("new@b.com")]
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("main.pocket.sync_from_pocket_id", return_value=new_users), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}), \
             patch("main.outline.fetch_outline_groups", return_value=groups), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={}), \
             patch("main.outline.build_outline_user_store", return_value=[]), \
             patch("main.outline.sync_group_memberships"), \
             patch("main.outline.sync_suspended_status") as mock_suspend:
            result = main._run_sync(force=False)
        assert result is None
        mock_suspend.assert_called_once()


# ---------------------------------------------------------------------------
# _is_force_due
# ---------------------------------------------------------------------------

class TestIsForceDue:
    def test_force_due_when_interval_elapsed(self):
        last = datetime.now(timezone.utc) - timedelta(seconds=main.FORCE_SYNC_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        assert main._is_force_due(last, now) is True

    def test_force_not_due_when_interval_not_elapsed(self):
        last = datetime.now(timezone.utc) - timedelta(seconds=main.FORCE_SYNC_INTERVAL_SECONDS - 1)
        now = datetime.now(timezone.utc)
        assert main._is_force_due(last, now) is False

    def test_force_due_exactly_at_boundary(self):
        last = datetime.now(timezone.utc) - timedelta(seconds=main.FORCE_SYNC_INTERVAL_SECONDS)
        now = last + timedelta(seconds=main.FORCE_SYNC_INTERVAL_SECONDS)
        assert main._is_force_due(last, now) is True


# ---------------------------------------------------------------------------
# POST /outline/force-sync
# ---------------------------------------------------------------------------

class TestForceSyncOutlineEndpoint:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_missing_api_key_returns_422(self):
        response = client.post("/outline/force-sync")
        assert response.status_code == 422

    def test_wrong_api_key_returns_403(self):
        response = client.post("/outline/force-sync", headers={"x-api-key": "wrong"})
        assert response.status_code == 403

    def test_empty_pocket_store_returns_404(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[]), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}):
            response = client.post("/outline/force-sync", headers={"x-api-key": API_KEY})
        assert response.status_code == 404

    def test_empty_pocket_groups_returns_404(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value=set()):
            response = client.post("/outline/force-sync", headers={"x-api-key": API_KEY})
        assert response.status_code == 404

    def test_successful_sync_returns_ok(self):
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}), \
             patch("main.outline.fetch_outline_groups", return_value=groups), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={"Group A": "g1"}), \
             patch("main.outline.build_outline_user_store", return_value=[]), \
             patch("main.outline.sync_group_memberships"), \
             patch("main.outline.sync_suspended_status"):
            response = client.post("/outline/force-sync", headers={"x-api-key": API_KEY})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_outline_sync_functions_called(self):
        groups = [{"id": "g1", "name": "Group A"}]
        outline_users = []
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}), \
             patch("main.outline.fetch_outline_groups", return_value=groups), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={"Group A": "g1"}), \
             patch("main.outline.build_outline_user_store", return_value=outline_users) as mock_build, \
             patch("main.outline.sync_group_memberships") as mock_sync, \
             patch("main.outline.sync_suspended_status") as mock_suspend:
            client.post("/outline/force-sync", headers={"x-api-key": API_KEY})
        mock_build.assert_called_once_with(groups)
        mock_sync.assert_called_once()
        mock_suspend.assert_called_once()

    def test_outline_user_store_built_once(self):
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}), \
             patch("main.outline.fetch_outline_groups", return_value=groups), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={}), \
             patch("main.outline.build_outline_user_store", return_value=[]) as mock_build, \
             patch("main.outline.sync_group_memberships"), \
             patch("main.outline.sync_suspended_status"):
            client.post("/outline/force-sync", headers={"x-api-key": API_KEY})
        assert mock_build.call_count == 1


# ---------------------------------------------------------------------------
# Old /outline/sync route removed
# ---------------------------------------------------------------------------

class TestOldSyncRouteRemoved:
    def test_outline_sync_returns_404(self):
        response = client.post("/outline/sync", headers={"x-api-key": API_KEY})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Startup sync (lifespan)
# ---------------------------------------------------------------------------

class TestStartupSync:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_startup_runs_force_sync(self):
        with patch("main._run_sync", return_value=None) as mock_sync:
            with TestClient(main.app):
                pass
        mock_sync.assert_called_once_with(force=True)

    def test_startup_sync_failure_does_not_prevent_startup(self):
        with patch("main._run_sync", side_effect=Exception("boom")):
            with TestClient(main.app):
                pass  # lifespan completes without re-raising


# ---------------------------------------------------------------------------
# GET /ssh/validate
# ---------------------------------------------------------------------------

class TestSshValidateEndpoint:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_missing_api_key_returns_422(self):
        response = client.get("/ssh/validate", params={"pubkey": "ssh-ed25519 AAAA"})
        assert response.status_code == 422

    def test_wrong_api_key_returns_403(self):
        response = client.get("/ssh/validate",
                              params={"pubkey": "ssh-ed25519 AAAA"},
                              headers={"x-api-key": "wrong"})
        assert response.status_code == 403

    def test_missing_pubkey_param_returns_422(self):
        response = client.get("/ssh/validate", headers={"x-api-key": API_KEY})
        assert response.status_code == 422

    def test_valid_key_returns_200_with_key_body(self):
        key = "ssh-ed25519 AAAA"
        with patch("main.pocket.sync_from_pocket_id", return_value=[]), \
             patch("main.ssh.validate_pubkey", return_value=key):
            response = client.get("/ssh/validate",
                                  params={"pubkey": key},
                                  headers={"x-api-key": API_KEY})
        assert response.status_code == 200
        assert response.text == key + "\n"

    def test_invalid_key_returns_204(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[]), \
             patch("main.ssh.validate_pubkey", return_value=None):
            response = client.get("/ssh/validate",
                                  params={"pubkey": "ssh-ed25519 AAAA"},
                                  headers={"x-api-key": API_KEY})
        assert response.status_code == 204

    def test_delegates_pubkey_and_store_to_ssh_module(self):
        key = "ssh-ed25519 AAAA"
        with patch("main.pocket.sync_from_pocket_id", return_value=[]), \
             patch("main.ssh.validate_pubkey", return_value=None) as mock_validate:
            client.get("/ssh/validate", params={"pubkey": key}, headers={"x-api-key": API_KEY})
        mock_validate.assert_called_once_with(key, main.pocket_userstore)

    def test_uses_cached_store_when_fresh(self):
        users = [_pu("a@b.com")]
        main.pocket_userstore = users
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main.pocket.sync_from_pocket_id") as mock_sync, \
             patch("main.ssh.validate_pubkey", return_value=None):
            client.get("/ssh/validate",
                       params={"pubkey": "ssh-ed25519 AAAA"},
                       headers={"x-api-key": API_KEY})
        mock_sync.assert_not_called()

    def test_pubkey_exceeding_8192_chars_returns_422(self):
        oversized = "ssh-ed25519 " + "A" * 8192
        response = client.get("/ssh/validate",
                              params={"pubkey": oversized},
                              headers={"x-api-key": API_KEY})
        assert response.status_code == 422
