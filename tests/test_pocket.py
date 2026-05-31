import pytest
import requests as requests_lib
from unittest.mock import call, patch, MagicMock

import pocket
from pocket import PocketUser, _paginate, sync_from_pocket_id, get_unique_groups


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_user(uid="u1", username="alice", email="alice@example.com",
              groups=None, claims=None, disabled=False):
    return {
        "id": uid,
        "username": username,
        "email": email,
        "userGroups": [{"name": g} for g in (groups or [])],
        "customClaims": claims or [],
        "disabled": disabled,
    }


def _page_response(data, total_pages=1, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "data": data,
        "pagination": {"totalPages": total_pages},
    }
    return mock


# ---------------------------------------------------------------------------
# _paginate
# ---------------------------------------------------------------------------

class TestPaginate:
    def test_single_page_returns_all_items(self):
        with patch("pocket.requests.get", return_value=_page_response([{"id": "1"}, {"id": "2"}])):
            result = _paginate("users")
        assert result == [{"id": "1"}, {"id": "2"}]

    def test_multiple_pages_are_concatenated(self):
        responses = [
            _page_response([{"id": "1"}], total_pages=2),
            _page_response([{"id": "2"}], total_pages=2),
        ]
        with patch("pocket.requests.get", side_effect=responses):
            result = _paginate("users")
        assert result == [{"id": "1"}, {"id": "2"}]

    def test_empty_first_page_returns_empty_list(self):
        with patch("pocket.requests.get", return_value=_page_response([])):
            result = _paginate("users")
        assert result == []

    def test_uses_correct_path(self):
        with patch("pocket.requests.get", return_value=_page_response([])) as mock_get:
            _paginate("user-groups")
        call_url = mock_get.call_args[0][0]
        assert call_url.endswith("/api/user-groups")

    def test_4xx_raises_immediately_without_retry(self):
        mock = MagicMock()
        mock.status_code = 403
        mock.raise_for_status.side_effect = requests_lib.HTTPError("HTTP 403")
        with patch("pocket.requests.get", return_value=mock) as mock_get, \
             patch("pocket.time.sleep"):
            with pytest.raises(requests_lib.HTTPError):
                _paginate("users")
        assert mock_get.call_count == 1

    def test_connection_error_is_retried(self):
        ok = _page_response([])
        with patch("pocket.requests.get", side_effect=[
            requests_lib.ConnectionError("refused"),
            ok,
        ]), patch("pocket.time.sleep"):
            result = _paginate("users")
        assert result == []

    def test_timeout_is_retried(self):
        ok = _page_response([])
        with patch("pocket.requests.get", side_effect=[
            requests_lib.Timeout("timed out"),
            ok,
        ]), patch("pocket.time.sleep"):
            result = _paginate("users")
        assert result == []

    def test_5xx_is_retried(self):
        err = MagicMock()
        err.status_code = 503
        ok = _page_response([])
        with patch("pocket.requests.get", side_effect=[err, ok]), \
             patch("pocket.time.sleep"):
            result = _paginate("users")
        assert result == []

    def test_exhausted_retries_raises(self):
        exc = requests_lib.ConnectionError("refused")
        with patch("pocket.requests.get", side_effect=exc), \
             patch("pocket.time.sleep"):
            with pytest.raises(requests_lib.ConnectionError):
                _paginate("users")

    def test_retry_sleeps_with_backoff(self):
        exc = requests_lib.ConnectionError("refused")
        with patch("pocket.requests.get", side_effect=exc), \
             patch("pocket.time.sleep") as mock_sleep:
            with pytest.raises(requests_lib.ConnectionError):
                _paginate("users")
        assert mock_sleep.call_args_list == [call(1), call(3)]

    def test_retry_logs_warning(self, caplog):
        exc = requests_lib.ConnectionError("refused")
        with patch("pocket.requests.get", side_effect=exc), \
             patch("pocket.time.sleep"):
            with caplog.at_level("WARNING", logger="uvicorn"):
                with pytest.raises(requests_lib.ConnectionError):
                    _paginate("users")
        assert caplog.text.count("retrying") == 2


# ---------------------------------------------------------------------------
# sync_from_pocket_id
# ---------------------------------------------------------------------------

class TestSyncFromPocketId:
    def test_returns_all_users_including_disabled(self):
        raw = [
            _raw_user("u1", disabled=False),
            _raw_user("u2", "bob", "bob@example.com", disabled=True),
        ]
        with patch("pocket._paginate", return_value=raw):
            result = sync_from_pocket_id()
        assert len(result) == 2
        assert result[0].disabled is False
        assert result[1].disabled is True

    def test_maps_all_fields(self):
        claims = [{"key": "ssh-pubkey", "value": "ssh-ed25519 AAAA"}]
        raw = [_raw_user("u1", "alice", "alice@example.com", ["Group A", "Group B"], claims)]
        with patch("pocket._paginate", return_value=raw):
            u = sync_from_pocket_id()[0]
        assert u.username == "alice"
        assert u.user_id == "u1"
        assert u.email == "alice@example.com"
        assert u.groups == ["Group A", "Group B"]
        assert u.custom_claims == claims
        assert u.disabled is False

    def test_user_with_no_groups(self):
        with patch("pocket._paginate", return_value=[_raw_user(groups=[])]):
            result = sync_from_pocket_id()
        assert result[0].groups == []

    def test_empty_response_returns_empty_list(self):
        with patch("pocket._paginate", return_value=[]):
            assert sync_from_pocket_id() == []

    def test_calls_paginate_with_users_path(self):
        with patch("pocket._paginate", return_value=[]) as mock_pag:
            sync_from_pocket_id()
        mock_pag.assert_called_once_with("users")


# ---------------------------------------------------------------------------
# get_unique_groups
# ---------------------------------------------------------------------------

class TestGetUniqueGroups:
    def test_returns_set_of_group_names(self):
        raw = [{"id": "g1", "name": "Group A"}, {"id": "g2", "name": "Group B"}]
        with patch("pocket._paginate", return_value=raw):
            result = get_unique_groups()
        assert result == {"Group A", "Group B"}

    def test_empty_returns_empty_set(self):
        with patch("pocket._paginate", return_value=[]):
            assert get_unique_groups() == set()

    def test_calls_paginate_with_user_groups_path(self):
        with patch("pocket._paginate", return_value=[]) as mock_pag:
            get_unique_groups()
        mock_pag.assert_called_once_with("user-groups")

    def test_deduplicates_names(self):
        raw = [{"id": "g1", "name": "Group A"}, {"id": "g2", "name": "Group A"}]
        with patch("pocket._paginate", return_value=raw):
            result = get_unique_groups()
        assert result == {"Group A"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class TestLogging:
    def test_sync_from_pocket_id_logs_user_count(self, caplog):
        raw = [_raw_user("u1"), _raw_user("u2")]
        with patch("pocket._paginate", return_value=raw):
            with caplog.at_level("INFO", logger="uvicorn"):
                sync_from_pocket_id()
        assert "Fetched 2 users from PocketID" in caplog.text

    def test_sync_from_pocket_id_logs_zero(self, caplog):
        with patch("pocket._paginate", return_value=[]):
            with caplog.at_level("INFO", logger="uvicorn"):
                sync_from_pocket_id()
        assert "Fetched 0 users from PocketID" in caplog.text

    def test_get_unique_groups_logs_group_count(self, caplog):
        raw = [{"id": "g1", "name": "A"}, {"id": "g2", "name": "B"}]
        with patch("pocket._paginate", return_value=raw):
            with caplog.at_level("INFO", logger="uvicorn"):
                get_unique_groups()
        assert "Found 2 groups in PocketID" in caplog.text

    def test_get_unique_groups_logs_zero(self, caplog):
        with patch("pocket._paginate", return_value=[]):
            with caplog.at_level("INFO", logger="uvicorn"):
                get_unique_groups()
        assert "Found 0 groups in PocketID" in caplog.text
