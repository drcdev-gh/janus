import hmac
import pytest
import socket
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
             patch("main.outline.build_outline_user_store", return_value=[]), \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={}), \
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

    def test_empty_user_list_logs_warning(self, caplog):
        with patch("main.pocket.sync_from_pocket_id", return_value=[]):
            with caplog.at_level("WARNING", logger="uvicorn"):
                main._run_sync(force=True)
        assert "empty user list" in caplog.text

    def test_empty_group_list_logs_warning(self, caplog):
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value=set()):
            with caplog.at_level("WARNING", logger="uvicorn"):
                main._run_sync(force=True)
        assert "empty group list" in caplog.text


# ---------------------------------------------------------------------------
# _run_sync — pre-write safety guards
# ---------------------------------------------------------------------------

class TestRunSyncGuards:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def _full_sync_patches(self, pocket_users, outline_users_list):
        groups = [{"id": "g1", "name": "Group A"}]
        return [
            patch("main.pocket.sync_from_pocket_id", return_value=pocket_users),
            patch("main.pocket.get_unique_groups", return_value={"Group A"}),
            patch("main.outline.fetch_outline_groups", return_value=groups),
            patch("main.outline.build_outline_user_store", return_value=outline_users_list),
            patch("main.outline.create_missing_groups", return_value=groups),
            patch("main.outline.delete_extra_groups", return_value=groups),
            patch("main.outline.build_group_name_to_id", return_value={}),
            patch("main.outline.sync_group_memberships"),
            patch("main.outline.sync_suspended_status"),
        ]

    def test_stale_cache_aborts_before_any_outline_write(self):
        main.pocket_userstore = [_pu("a@b.com")]
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=main.SYNC_INTERVAL_SECONDS * 1.2
        )
        with patch("main.update_pocket_userstore", return_value=True), \
             patch("main.outline.fetch_outline_groups") as mock_fetch, \
             patch("main.outline.create_missing_groups") as mock_create:
            result = main._run_sync(force=True)
        assert result is not None
        assert "stale" in result.lower()
        mock_fetch.assert_not_called()
        mock_create.assert_not_called()

    def test_stale_cache_logs_warning(self, caplog):
        main.pocket_userstore = [_pu("a@b.com")]
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=main.SYNC_INTERVAL_SECONDS * 1.2
        )
        with patch("main.update_pocket_userstore", return_value=True):
            with caplog.at_level("WARNING", logger="uvicorn"):
                main._run_sync(force=True)
        assert "stale" in caplog.text

    def test_outline_exceeds_pocket_count_aborts_all_writes(self):
        pocket_users = [_pu("a@b.com")]            # 1 PocketID user
        outline_users = [MagicMock(), MagicMock()]  # 2 Outline users
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in self._full_sync_patches(pocket_users, outline_users)]
            result = main._run_sync(force=True)
        mock_create, mock_delete, mock_memberships, mock_suspend = mocks[4], mocks[5], mocks[7], mocks[8]
        assert result is not None
        assert "outline" in result.lower()
        mock_create.assert_not_called()
        mock_delete.assert_not_called()
        mock_memberships.assert_not_called()
        mock_suspend.assert_not_called()

    def test_outline_exceeds_pocket_count_logs_warning_with_counts(self, caplog):
        pocket_users = [_pu("a@b.com")]
        outline_users = [MagicMock(), MagicMock()]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._full_sync_patches(pocket_users, outline_users):
                stack.enter_context(p)
            with caplog.at_level("WARNING", logger="uvicorn"):
                main._run_sync(force=True)
        assert "2" in caplog.text and "1" in caplog.text

    def test_equal_counts_does_not_abort(self):
        pocket_users = [_pu("a@b.com")]
        outline_users = [MagicMock()]  # 1 == 1, should proceed
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in self._full_sync_patches(pocket_users, outline_users)]
            result = main._run_sync(force=True)
        mock_suspend = mocks[8]
        assert result is None
        mock_suspend.assert_called_once()


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
             patch("main.outline.build_outline_user_store", return_value=outline_users) as mock_build, \
             patch("main.outline.create_missing_groups", return_value=groups), \
             patch("main.outline.delete_extra_groups", return_value=groups), \
             patch("main.outline.build_group_name_to_id", return_value={"Group A": "g1"}), \
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
        main.pocket_userstore = []
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main.ssh.validate_pubkey", return_value=key):
            response = client.get("/ssh/validate",
                                  params={"pubkey": key},
                                  headers={"x-api-key": API_KEY})
        assert response.status_code == 200
        assert response.text == key + "\n"

    def test_invalid_key_returns_204(self):
        main.pocket_userstore = []
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main.ssh.validate_pubkey", return_value=None):
            response = client.get("/ssh/validate",
                                  params={"pubkey": "ssh-ed25519 AAAA"},
                                  headers={"x-api-key": API_KEY})
        assert response.status_code == 204

    def test_delegates_pubkey_and_store_to_ssh_module(self):
        key = "ssh-ed25519 AAAA"
        main.pocket_userstore = []
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main.ssh.validate_pubkey", return_value=None) as mock_validate:
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

    def test_empty_cache_returns_204(self):
        # last_updated_timestamp is None from setup_method
        with patch("main.ssh.validate_pubkey") as mock_validate:
            response = client.get("/ssh/validate",
                                  params={"pubkey": "ssh-ed25519 AAAA"},
                                  headers={"x-api-key": API_KEY})
        assert response.status_code == 204
        mock_validate.assert_not_called()

    def test_stale_cache_returns_204_without_validating(self):
        main.pocket_userstore = [_pu("a@b.com")]
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=main.SYNC_INTERVAL_SECONDS * 1.2
        )
        with patch("main.ssh.validate_pubkey") as mock_validate:
            response = client.get("/ssh/validate",
                                  params={"pubkey": "ssh-ed25519 AAAA"},
                                  headers={"x-api-key": API_KEY})
        assert response.status_code == 204
        mock_validate.assert_not_called()

    def test_cache_at_grace_boundary_allows_validation(self):
        main.pocket_userstore = []
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=main.SYNC_INTERVAL_SECONDS * 1.05
        )
        with patch("main.ssh.validate_pubkey", return_value=None) as mock_validate:
            client.get("/ssh/validate",
                       params={"pubkey": "ssh-ed25519 AAAA"},
                       headers={"x-api-key": API_KEY})
        mock_validate.assert_called_once()

    def test_ssh_validate_never_calls_pocket_sync(self):
        main.pocket_userstore = []
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


# ---------------------------------------------------------------------------
# _ping_host
# ---------------------------------------------------------------------------

class TestPingHost:
    def test_opens_tcp_connection_to_https_host(self):
        with patch("socket.create_connection", return_value=MagicMock()) as mock_conn:
            main._ping_host("https://example.com")
        mock_conn.assert_called_once_with(("example.com", 443), timeout=3.0)

    def test_uses_explicit_port_when_present(self):
        with patch("socket.create_connection", return_value=MagicMock()) as mock_conn:
            main._ping_host("https://example.com:8443")
        mock_conn.assert_called_once_with(("example.com", 8443), timeout=3.0)

    def test_raises_on_connection_failure(self):
        with patch("socket.create_connection", side_effect=OSError("refused")):
            with pytest.raises(OSError):
                main._ping_host("https://example.com")


# ---------------------------------------------------------------------------
# last_sync_error tracking
# ---------------------------------------------------------------------------

class TestLastSyncError:
    def setup_method(self):
        main.last_sync_error = None
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_startup_clears_error_on_success(self):
        main.last_sync_error = "previous error"
        with patch("main._run_sync", return_value=None):
            with TestClient(main.app):
                pass
        assert main.last_sync_error is None

    def test_startup_sets_error_on_skip(self):
        with patch("main._run_sync", return_value="empty Pocket user store"):
            with TestClient(main.app):
                pass
        assert main.last_sync_error == "empty Pocket user store"

    def test_startup_sets_error_on_exception(self):
        with patch("main._run_sync", side_effect=Exception("API down")):
            with TestClient(main.app):
                pass
        assert main.last_sync_error == "API down"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def setup_method(self):
        main.last_sync_error = None
        main._health_cache = None
        main._health_cache_time = None
        main.last_updated_timestamp = datetime.now(timezone.utc)

    def test_healthy_when_all_checks_pass(self):
        with patch("main._ping_host"):
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "healthy",
            "checks": {"pocketid": "ok", "outline": "ok", "last_sync": "ok", "ssh_cache": "ok"},
        }

    def test_unhealthy_when_pocketid_unreachable(self):
        pocket_url = "https://pocket.test"
        def side_effect(url, **kwargs):
            if url == pocket_url:
                raise OSError("connection refused")
        with patch("main._ping_host", side_effect=side_effect):
            response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["pocketid"] == "connection refused"
        assert data["checks"]["outline"] == "ok"
        assert data["checks"]["last_sync"] == "ok"

    def test_unhealthy_when_outline_unreachable(self):
        outline_url = "https://outline.test"
        def side_effect(url, **kwargs):
            if url == outline_url:
                raise OSError("timeout")
        with patch("main._ping_host", side_effect=side_effect):
            response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["pocketid"] == "ok"
        assert data["checks"]["outline"] == "timeout"

    def test_unhealthy_when_last_sync_failed(self):
        main.last_sync_error = "empty Pocket user store"
        with patch("main._ping_host"):
            response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["checks"]["last_sync"] == "empty Pocket user store"

    def test_unhealthy_shows_all_failing_checks(self):
        main.last_sync_error = "API timeout"
        with patch("main._ping_host", side_effect=OSError("refused")):
            response = client.get("/health")
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["pocketid"] != "ok"
        assert data["checks"]["outline"] != "ok"
        assert data["checks"]["last_sync"] == "API timeout"

    def test_cache_prevents_second_ping_within_60s(self):
        with patch("main._ping_host") as mock_ping:
            client.get("/health")
            client.get("/health")
        assert mock_ping.call_count == 2  # 2 pings on first request only

    def test_cache_returns_same_body_on_second_request(self):
        with patch("main._ping_host"):
            r1 = client.get("/health")
            r2 = client.get("/health")
        assert r1.json() == r2.json()

    def test_cache_expires_after_60s(self):
        with patch("main._ping_host") as mock_ping:
            client.get("/health")
            main._health_cache_time = datetime.now(timezone.utc) - timedelta(seconds=61)
            client.get("/health")
        assert mock_ping.call_count == 4  # 2 pings × 2 requests

    def test_cached_unhealthy_response_returns_503(self):
        main.last_sync_error = "sync failed"
        with patch("main._ping_host"):
            client.get("/health")  # populates cache as unhealthy
            main.last_sync_error = None  # simulate recovery
            response = client.get("/health")  # should still return cached 503
        assert response.status_code == 503

    def test_healthy_includes_ssh_cache_ok_when_fresh(self):
        main.last_updated_timestamp = datetime.now(timezone.utc)
        with patch("main._ping_host"):
            response = client.get("/health")
        assert response.json()["checks"]["ssh_cache"] == "ok"
        assert response.status_code == 200

    def test_unhealthy_when_ssh_cache_is_none(self):
        main.last_updated_timestamp = None
        with patch("main._ping_host"):
            response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["checks"]["ssh_cache"] == "stale"

    def test_unhealthy_when_ssh_cache_is_stale(self):
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=main.SYNC_INTERVAL_SECONDS * 1.2
        )
        with patch("main._ping_host"):
            response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["checks"]["ssh_cache"] == "stale"

    def test_ssh_cache_ok_within_grace_window(self):
        main.last_updated_timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=main.SYNC_INTERVAL_SECONDS * 1.05
        )
        with patch("main._ping_host"):
            response = client.get("/health")
        assert response.json()["checks"]["ssh_cache"] == "ok"


# ---------------------------------------------------------------------------
# DRY_RUN mode (main.py)
# ---------------------------------------------------------------------------

class TestDryRunMain:
    def setup_method(self):
        main.DRY_RUN = True
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def teardown_method(self):
        main.DRY_RUN = False

    def test_ssh_validate_returns_204_immediately(self):
        response = client.get("/ssh/validate",
                              params={"pubkey": "ssh-ed25519 AAAA"},
                              headers={"x-api-key": API_KEY})
        assert response.status_code == 204
        assert response.text == ""

    def test_ssh_validate_skips_pocket_lookup(self):
        with patch("main.pocket.sync_from_pocket_id") as mock_sync:
            client.get("/ssh/validate",
                       params={"pubkey": "ssh-ed25519 AAAA"},
                       headers={"x-api-key": API_KEY})
        mock_sync.assert_not_called()

    def test_ssh_validate_still_checks_api_key(self):
        response = client.get("/ssh/validate",
                              params={"pubkey": "ssh-ed25519 AAAA"},
                              headers={"x-api-key": "wrong"})
        assert response.status_code == 403

    def test_lifespan_skips_scheduled_sync(self):
        with patch("main._run_sync", return_value=None), \
             patch("main._scheduled_sync") as mock_sched:
            with TestClient(main.app):
                pass
        mock_sched.assert_not_called()

    def test_lifespan_still_runs_startup_sync(self):
        with patch("main._run_sync", return_value=None) as mock_sync:
            with TestClient(main.app):
                pass
        mock_sync.assert_called_once_with(force=True)

    def test_lifespan_logs_dry_run_warning(self, caplog):
        with patch("main._run_sync", return_value=None):
            with caplog.at_level("WARNING", logger="uvicorn"):
                with TestClient(main.app):
                    pass
        assert "DRY RUN" in caplog.text
