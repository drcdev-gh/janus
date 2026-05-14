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
# GET /outline/sync
# ---------------------------------------------------------------------------

class TestSyncOutlineEndpoint:
    def setup_method(self):
        main.pocket_userstore = None
        main.last_updated_timestamp = None

    def test_missing_api_key_returns_422(self):
        response = client.get("/outline/sync")
        assert response.status_code == 422

    def test_wrong_api_key_returns_403(self):
        response = client.get("/outline/sync", headers={"x-api-key": "wrong"})
        assert response.status_code == 403

    def test_empty_pocket_store_returns_404(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[]), \
             patch("main.pocket.get_unique_groups", return_value={"Group A"}):
            response = client.get("/outline/sync", headers={"x-api-key": API_KEY})
        assert response.status_code == 404

    def test_empty_pocket_groups_returns_404(self):
        with patch("main.pocket.sync_from_pocket_id", return_value=[_pu("a@b.com")]), \
             patch("main.pocket.get_unique_groups", return_value=set()):
            response = client.get("/outline/sync", headers={"x-api-key": API_KEY})
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
            response = client.get("/outline/sync", headers={"x-api-key": API_KEY})
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
            client.get("/outline/sync", headers={"x-api-key": API_KEY})
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
            client.get("/outline/sync", headers={"x-api-key": API_KEY})
        assert mock_build.call_count == 1


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
