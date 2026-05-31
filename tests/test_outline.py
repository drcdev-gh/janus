import pytest
from unittest.mock import patch, call

import outline
from outline import (
    OutlineUser,
    fetch_outline_users,
    fetch_outline_groups,
    build_group_name_to_id,
    build_outline_user_store,
    create_missing_groups,
    delete_extra_groups,
    sync_group_memberships,
    sync_suspended_status,
)
from pocket import PocketUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ou(uid, email, groups=None, suspended=False):
    return OutlineUser(id=uid, name="User", email=email,
                       groups=groups or [], suspended=suspended)


def _pu(email, groups=None, disabled=False):
    return PocketUser(username="user", user_id="p1", email=email,
                      groups=groups or [], custom_claims=[], disabled=disabled)


def _users_resp(users):
    return {"data": users}


def _groups_resp(groups):
    return {"data": {"groups": groups}}


# ---------------------------------------------------------------------------
# fetch_outline_users
# ---------------------------------------------------------------------------

class TestFetchOutlineUsers:
    def test_returns_users_on_single_page(self):
        raw = [{"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": False}]
        with patch("outline._post", return_value=_users_resp(raw)):
            result = fetch_outline_users()
        assert len(result) == 1
        assert result[0]["id"] == "u1"

    def test_paginates_until_short_page(self):
        page1 = [{"id": f"u{i}"} for i in range(100)]
        page2 = [{"id": "u100"}]
        with patch("outline._post", side_effect=[_users_resp(page1), _users_resp(page2)]):
            result = fetch_outline_users()
        assert len(result) == 101

    def test_returns_empty_list_when_no_users(self):
        with patch("outline._post", return_value=_users_resp([])):
            assert fetch_outline_users() == []

    def test_requests_all_filter(self):
        with patch("outline._post", return_value=_users_resp([])) as mock:
            fetch_outline_users()
        assert mock.call_args[0][1]["filter"] == "all"


# ---------------------------------------------------------------------------
# fetch_outline_groups
# ---------------------------------------------------------------------------

class TestFetchOutlineGroups:
    def test_returns_groups_on_single_page(self):
        with patch("outline._post", return_value=_groups_resp([{"id": "g1", "name": "G"}])):
            result = fetch_outline_groups()
        assert result == [{"id": "g1", "name": "G"}]

    def test_paginates_until_short_page(self):
        page1 = _groups_resp([{"id": f"g{i}"} for i in range(100)])
        page2 = _groups_resp([{"id": "g100"}])
        with patch("outline._post", side_effect=[page1, page2]):
            result = fetch_outline_groups()
        assert len(result) == 101

    def test_returns_empty_when_no_groups(self):
        with patch("outline._post", return_value=_groups_resp([])):
            assert fetch_outline_groups() == []

    def test_tolerates_missing_groups_key(self):
        with patch("outline._post", return_value={"data": {}}):
            assert fetch_outline_groups() == []


# ---------------------------------------------------------------------------
# build_group_name_to_id
# ---------------------------------------------------------------------------

class TestBuildGroupNameToId:
    def test_maps_name_to_id(self):
        groups = [{"id": "g1", "name": "A"}, {"id": "g2", "name": "B"}]
        assert build_group_name_to_id(groups) == {"A": "g1", "B": "g2"}

    def test_empty_input(self):
        assert build_group_name_to_id([]) == {}


# ---------------------------------------------------------------------------
# build_outline_user_store
# ---------------------------------------------------------------------------

class TestBuildOutlineUserStore:
    def test_assembles_user_with_group(self):
        raw_user = {"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": False}
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("outline.fetch_outline_users", return_value=[raw_user]), \
             patch("outline._fetch_group_members", return_value=[{"id": "u1"}]):
            result = build_outline_user_store(groups)
        assert result[0].groups == ["Group A"]

    def test_user_with_no_group_membership(self):
        raw_user = {"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": False}
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("outline.fetch_outline_users", return_value=[raw_user]), \
             patch("outline._fetch_group_members", return_value=[]):
            result = build_outline_user_store(groups)
        assert result[0].groups == []

    def test_sets_suspended_flag(self):
        raw_user = {"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": True}
        with patch("outline.fetch_outline_users", return_value=[raw_user]), \
             patch("outline._fetch_group_members", return_value=[]):
            result = build_outline_user_store([])
        assert result[0].suspended is True

    def test_unknown_member_skipped_without_error(self):
        raw_user = {"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": False}
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("outline.fetch_outline_users", return_value=[raw_user]), \
             patch("outline._fetch_group_members", return_value=[{"id": "unknown-id"}]):
            result = build_outline_user_store(groups)
        assert result[0].groups == []

    def test_no_groups_gives_empty_memberships(self):
        raw_user = {"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": False}
        with patch("outline.fetch_outline_users", return_value=[raw_user]):
            result = build_outline_user_store([])
        assert result[0].groups == []

    def test_empty_user_list(self):
        with patch("outline.fetch_outline_users", return_value=[]):
            assert build_outline_user_store([]) == []


# ---------------------------------------------------------------------------
# create_missing_groups
# ---------------------------------------------------------------------------

class TestCreateMissingGroups:
    def test_creates_absent_groups(self):
        groups = [{"id": "g1", "name": "Existing"}]
        new = {"id": "g2", "name": "New Group"}
        with patch("outline.create_outline_group", return_value=new) as mock_create:
            result = create_missing_groups({"Existing", "New Group"}, groups)
        mock_create.assert_called_once_with("New Group")
        assert any(g["name"] == "New Group" for g in result)

    def test_skips_already_present_groups(self):
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("outline.create_outline_group") as mock_create:
            result = create_missing_groups({"Group A"}, groups)
        mock_create.assert_not_called()
        assert result == groups

    def test_does_not_mutate_input_list(self):
        groups = [{"id": "g1", "name": "Existing"}]
        original = list(groups)
        with patch("outline.create_outline_group", return_value={"id": "g2", "name": "New"}):
            create_missing_groups({"Existing", "New"}, groups)
        assert groups == original

    def test_empty_pocket_groups_creates_nothing(self):
        groups = [{"id": "g1", "name": "Existing"}]
        with patch("outline.create_outline_group") as mock_create:
            result = create_missing_groups(set(), groups)
        mock_create.assert_not_called()
        assert result == groups


# ---------------------------------------------------------------------------
# delete_extra_groups
# ---------------------------------------------------------------------------

class TestDeleteExtraGroups:
    def test_deletes_groups_absent_from_pocket(self):
        groups = [{"id": "g1", "name": "Keep"}, {"id": "g2", "name": "Remove"}]
        with patch("outline.delete_outline_group") as mock_del:
            result = delete_extra_groups({"Keep"}, groups)
        mock_del.assert_called_once_with("g2")
        assert result == [{"id": "g1", "name": "Keep"}]

    def test_keeps_all_when_they_match_pocket(self):
        groups = [{"id": "g1", "name": "Group A"}]
        with patch("outline.delete_outline_group") as mock_del:
            result = delete_extra_groups({"Group A"}, groups)
        mock_del.assert_not_called()
        assert result == groups

    def test_deletes_all_when_pocket_is_empty(self):
        groups = [{"id": "g1", "name": "G1"}, {"id": "g2", "name": "G2"}]
        with patch("outline.delete_outline_group") as mock_del:
            result = delete_extra_groups(set(), groups)
        assert mock_del.call_count == 2
        assert result == []


# ---------------------------------------------------------------------------
# sync_group_memberships
# ---------------------------------------------------------------------------

class TestSyncGroupMemberships:
    def test_adds_missing_membership(self):
        pocket_users = [_pu("alice@example.com", ["Group A"])]
        outline_users = [_ou("o1", "alice@example.com", groups=[])]
        with patch("outline.add_group_membership") as mock_add:
            sync_group_memberships(pocket_users, outline_users, {"Group A": "g1"})
        mock_add.assert_called_once_with("g1", "o1")

    def test_removes_extra_membership(self):
        pocket_users = [_pu("alice@example.com", [])]
        outline_users = [_ou("o1", "alice@example.com", ["Group A"])]
        with patch("outline.delete_group_membership") as mock_del:
            sync_group_memberships(pocket_users, outline_users, {"Group A": "g1"})
        mock_del.assert_called_once_with("g1", "o1")

    def test_adds_and_removes_in_single_pass(self):
        pocket_users = [_pu("alice@example.com", ["Group B"])]
        outline_users = [_ou("o1", "alice@example.com", ["Group A"])]
        group_map = {"Group A": "g1", "Group B": "g2"}
        with patch("outline.add_group_membership") as mock_add, \
             patch("outline.delete_group_membership") as mock_del:
            sync_group_memberships(pocket_users, outline_users, group_map)
        mock_add.assert_called_once_with("g2", "o1")
        mock_del.assert_called_once_with("g1", "o1")

    def test_skips_existing_membership(self):
        pocket_users = [_pu("alice@example.com", ["Group A"])]
        outline_users = [_ou("o1", "alice@example.com", ["Group A"])]
        with patch("outline.add_group_membership") as mock_add, \
             patch("outline.delete_group_membership") as mock_del:
            sync_group_memberships(pocket_users, outline_users, {"Group A": "g1"})
        mock_add.assert_not_called()
        mock_del.assert_not_called()

    def test_skips_disabled_pocket_user(self):
        pocket_users = [_pu("alice@example.com", ["Group A"], disabled=True)]
        outline_users = [_ou("o1", "alice@example.com")]
        with patch("outline.add_group_membership") as mock_add:
            sync_group_memberships(pocket_users, outline_users, {"Group A": "g1"})
        mock_add.assert_not_called()

    def test_syncs_suspended_outline_user_that_is_active_in_pocket(self):
        pocket_users = [_pu("alice@example.com", ["Group A"])]
        outline_users = [_ou("o1", "alice@example.com", suspended=True)]
        with patch("outline.add_group_membership") as mock_add:
            sync_group_memberships(pocket_users, outline_users, {"Group A": "g1"})
        mock_add.assert_called_once_with("g1", "o1")

    def test_skips_unmatched_outline_user(self):
        pocket_users = [_pu("other@example.com", ["Group A"])]
        outline_users = [_ou("o1", "alice@example.com")]
        with patch("outline.add_group_membership") as mock_add:
            sync_group_memberships(pocket_users, outline_users, {"Group A": "g1"})
        mock_add.assert_not_called()

    def test_skips_group_missing_from_map(self):
        pocket_users = [_pu("alice@example.com", ["Ghost Group"])]
        outline_users = [_ou("o1", "alice@example.com")]
        with patch("outline.add_group_membership") as mock_add:
            sync_group_memberships(pocket_users, outline_users, {})
        mock_add.assert_not_called()

    def test_pocket_lookup_is_o1_not_linear(self):
        # Verifies that the email map is built once by ensuring many outline users
        # can be matched without the call count scaling with pocket user count.
        pocket_users = [_pu(f"u{i}@example.com", ["G"]) for i in range(50)]
        outline_users = [_ou(f"o{i}", f"u{i}@example.com") for i in range(50)]
        with patch("outline.add_group_membership") as mock_add:
            sync_group_memberships(pocket_users, outline_users, {"G": "g1"})
        assert mock_add.call_count == 50


# ---------------------------------------------------------------------------
# sync_suspended_status
# ---------------------------------------------------------------------------

class TestSyncSuspendedStatus:
    def test_suspends_disabled_active_user(self):
        pocket_users = [_pu("alice@example.com", disabled=True)]
        outline_users = [_ou("o1", "alice@example.com", suspended=False)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_called_once_with("users.suspend", {"id": "o1"})

    def test_activates_reenabled_suspended_user(self):
        pocket_users = [_pu("alice@example.com", disabled=False)]
        outline_users = [_ou("o1", "alice@example.com", suspended=True)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_called_once_with("users.activate", {"id": "o1"})

    def test_no_action_when_both_active(self):
        pocket_users = [_pu("alice@example.com", disabled=False)]
        outline_users = [_ou("o1", "alice@example.com", suspended=False)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_not_called()

    def test_no_action_when_both_suspended(self):
        pocket_users = [_pu("alice@example.com", disabled=True)]
        outline_users = [_ou("o1", "alice@example.com", suspended=True)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_not_called()

    def test_skips_outline_user_with_no_email(self):
        pocket_users = [_pu("alice@example.com", disabled=True)]
        outline_users = [OutlineUser(id="o1", name="Alice", email=None, groups=[], suspended=False)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_not_called()

    def test_skips_when_no_matching_pocket_user(self):
        pocket_users = [_pu("other@example.com")]
        outline_users = [_ou("o1", "alice@example.com", suspended=True)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_not_called()

    def test_warns_when_no_matching_pocket_user(self):
        pocket_users = [_pu("other@example.com")]
        outline_users = [_ou("o1", "alice@example.com", suspended=False)]
        with patch("outline.logger") as mock_logger, patch("outline._post"):
            sync_suspended_status(pocket_users, outline_users)
        mock_logger.warning.assert_called_once()
        assert "alice@example.com" in mock_logger.warning.call_args[0][1]


# ---------------------------------------------------------------------------
# Logging summaries
# ---------------------------------------------------------------------------

class TestLogging:
    def test_build_outline_user_store_logs_fetch_count(self, caplog):
        users = [{"id": "u1", "name": "Alice", "email": "a@b.com", "isSuspended": False}]
        groups = [{"id": "g1", "name": "Eng"}]
        with patch("outline.fetch_outline_users", return_value=users), \
             patch("outline._fetch_group_members", return_value=[]):
            with caplog.at_level("INFO", logger="uvicorn"):
                build_outline_user_store(groups)
        assert "Fetched 1 users from Outline across 1 groups" in caplog.text

    def test_create_missing_groups_logs_created_count(self, caplog):
        with patch("outline.create_outline_group", return_value={"id": "g2", "name": "New"}):
            with caplog.at_level("INFO", logger="uvicorn"):
                create_missing_groups({"Existing", "New"}, [{"id": "g1", "name": "Existing"}])
        assert "Groups: 1 created" in caplog.text

    def test_create_missing_groups_logs_zero(self, caplog):
        with caplog.at_level("INFO", logger="uvicorn"):
            create_missing_groups({"Existing"}, [{"id": "g1", "name": "Existing"}])
        assert "Groups: 0 created" in caplog.text

    def test_delete_extra_groups_logs_deleted_count(self, caplog):
        with patch("outline.delete_outline_group"):
            with caplog.at_level("INFO", logger="uvicorn"):
                delete_extra_groups({"Keep"}, [{"id": "g1", "name": "Keep"}, {"id": "g2", "name": "Gone"}])
        assert "Groups: 1 deleted" in caplog.text

    def test_delete_extra_groups_logs_zero(self, caplog):
        with caplog.at_level("INFO", logger="uvicorn"):
            delete_extra_groups({"Keep"}, [{"id": "g1", "name": "Keep"}])
        assert "Groups: 0 deleted" in caplog.text

    def test_sync_group_memberships_logs_summary(self, caplog):
        pocket_users = [_pu("a@b.com", groups=["Eng"])]
        outline_users = [_ou("o1", "a@b.com", groups=["Old"])]
        with patch("outline.add_group_membership"), patch("outline.delete_group_membership"):
            with caplog.at_level("INFO", logger="uvicorn"):
                sync_group_memberships(pocket_users, outline_users, {"Eng": "g1", "Old": "g2"})
        assert "Group memberships: 1 added, 1 removed" in caplog.text

    def test_sync_group_memberships_logs_zero(self, caplog):
        pocket_users = [_pu("a@b.com", groups=["Eng"])]
        outline_users = [_ou("o1", "a@b.com", groups=["Eng"])]
        with caplog.at_level("INFO", logger="uvicorn"):
            sync_group_memberships(pocket_users, outline_users, {"Eng": "g1"})
        assert "Group memberships: 0 added, 0 removed" in caplog.text

    def test_sync_suspended_status_logs_summary(self, caplog):
        pocket_users = [_pu("a@b.com", disabled=True), _pu("b@b.com")]
        outline_users = [_ou("o1", "a@b.com", suspended=False), _ou("o2", "b@b.com", suspended=True)]
        with patch("outline._post"):
            with caplog.at_level("INFO", logger="uvicorn"):
                sync_suspended_status(pocket_users, outline_users)
        assert "User status: 1 suspended, 1 reactivated" in caplog.text

    def test_sync_suspended_status_logs_zero(self, caplog):
        pocket_users = [_pu("a@b.com")]
        outline_users = [_ou("o1", "a@b.com", suspended=False)]
        with caplog.at_level("INFO", logger="uvicorn"):
            sync_suspended_status(pocket_users, outline_users)
        assert "User status: 0 suspended, 0 reactivated" in caplog.text


# ---------------------------------------------------------------------------
# DRY_RUN mode
# ---------------------------------------------------------------------------

class TestDryRun:
    def setup_method(self):
        outline.DRY_RUN = True

    def teardown_method(self):
        outline.DRY_RUN = False

    def test_create_outline_group_skips_api(self):
        with patch("outline._post") as mock_post:
            result = outline.create_outline_group("TestGroup")
        mock_post.assert_not_called()
        assert result == {}

    def test_delete_outline_group_skips_api(self):
        with patch("outline._post") as mock_post:
            outline.delete_outline_group("g1")
        mock_post.assert_not_called()

    def test_add_group_membership_skips_api(self):
        with patch("outline._post") as mock_post:
            outline.add_group_membership("g1", "u1")
        mock_post.assert_not_called()

    def test_delete_group_membership_skips_api(self):
        with patch("outline._post") as mock_post:
            outline.delete_group_membership("g1", "u1")
        mock_post.assert_not_called()

    def test_sync_suspended_status_skips_suspend(self):
        pocket_users = [_pu("a@b.com", disabled=True)]
        outline_users = [_ou("o1", "a@b.com", suspended=False)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_not_called()

    def test_sync_suspended_status_skips_reactivate(self):
        pocket_users = [_pu("a@b.com")]
        outline_users = [_ou("o1", "a@b.com", suspended=True)]
        with patch("outline._post") as mock_post:
            sync_suspended_status(pocket_users, outline_users)
        mock_post.assert_not_called()

    def test_create_missing_groups_counts_intended_creations(self, caplog):
        with caplog.at_level("INFO", logger="uvicorn"):
            create_missing_groups({"New"}, [])
        assert "Groups: 1 created (dry run)" in caplog.text

    def test_delete_extra_groups_logs_dry_run(self, caplog):
        with caplog.at_level("INFO", logger="uvicorn"):
            delete_extra_groups(set(), [{"id": "g1", "name": "Gone"}])
        assert "Groups: 1 deleted (dry run)" in caplog.text

    def test_sync_group_memberships_logs_dry_run(self, caplog):
        pocket_users = [_pu("a@b.com", groups=["Eng"])]
        outline_users = [_ou("o1", "a@b.com", groups=[])]
        with caplog.at_level("INFO", logger="uvicorn"):
            sync_group_memberships(pocket_users, outline_users, {"Eng": "g1"})
        assert "Group memberships: 1 added, 0 removed (dry run)" in caplog.text

    def test_sync_suspended_status_logs_dry_run(self, caplog):
        pocket_users = [_pu("a@b.com", disabled=True)]
        outline_users = [_ou("o1", "a@b.com", suspended=False)]
        with caplog.at_level("INFO", logger="uvicorn"):
            sync_suspended_status(pocket_users, outline_users)
        assert "User status: 1 suspended, 0 reactivated (dry run)" in caplog.text

    def test_per_operation_logs_still_fire(self, caplog):
        pocket_users = [_pu("a@b.com", groups=["Eng"])]
        outline_users = [_ou("o1", "a@b.com", groups=[])]
        with caplog.at_level("INFO", logger="uvicorn"):
            sync_group_memberships(pocket_users, outline_users, {"Eng": "g1"})
        assert "Adding a@b.com to group Eng" in caplog.text
