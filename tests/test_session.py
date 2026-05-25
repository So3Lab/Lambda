"""Tests for SessionManager."""

import json
import os
import tempfile

import pytest

from lambda_coding_agent.tui.session import SessionManager


@pytest.fixture()
def tmp_workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture()
def mgr(tmp_workspace):
    return SessionManager(tmp_workspace)


class TestSessionCreation:
    def test_start_new_session_returns_uuid4(self, mgr):
        sid = mgr.start_new_session()
        assert len(sid) == 36  # standard uuid4 hex format
        assert sid.count("-") == 4

    def test_creates_sessions_dir(self, mgr):
        sid = mgr.start_new_session()
        assert os.path.isdir(mgr.sessions_dir)

    def test_sessions_dir_nested_in_lambda(self, tmp_workspace):
        mgr = SessionManager(tmp_workspace)
        expected = os.path.join(tmp_workspace, ".lambda", "sessions")
        assert mgr.sessions_dir == expected


class TestSaveLoad:
    def test_save_and_load_roundtrip(self, mgr):
        sid = mgr.start_new_session()
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        mgr.save_session(
            sid,
            history=history,
            model_name="test/model",
            name="Test Session",
            provider_id="test",
            last_ctx_usage=5000,
        )
        data = mgr.load_session(sid)
        assert data["id"] == sid
        assert data["name"] == "Test Session"
        assert data["model_name"] == "test/model"
        assert data["provider_id"] == "test"
        assert data["last_ctx_usage"] == 5000
        assert data["history"] == history
        assert data["schema_version"] == 2
        assert len(data["message_nodes"]) == 2
        assert data["active_leaf_id"] in data["message_nodes"]
        assert data["created_at"]
        assert data["last_active_at"]

    def test_loads_legacy_flat_history_as_single_branch(self, mgr):
        sid = mgr.start_new_session()
        legacy = {
            "id": sid,
            "name": "Legacy",
            "created_at": "2024-01-01T00:00:00+00:00",
            "last_active_at": "2024-01-01T00:00:00+00:00",
            "model_name": "m",
            "history": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        }
        with open(mgr._session_path(sid), "w", encoding="utf-8") as f:
            json.dump(legacy, f)

        data = mgr.load_session(sid)

        assert data["schema_version"] == 2
        assert data["history"] == legacy["history"]
        assert len(data["message_nodes"]) == 2
        assert mgr.project_active_history(
            data["message_nodes"],
            data["active_leaf_id"],
        ) == legacy["history"]

    def test_saves_and_loads_branch_aware_session(self, mgr):
        sid = mgr.start_new_session()
        trunk = [{"role": "user", "content": "first"}]
        nodes, active_leaf_id, next_seq = mgr.history_to_message_tree(trunk)
        branch_leaf, next_seq = mgr.append_message_node(
            nodes,
            active_leaf_id,
            {"role": "assistant", "content": "branch"},
            next_seq,
        )
        mgr.append_message_node(
            nodes,
            active_leaf_id,
            {"role": "assistant", "content": "other branch"},
            next_seq,
        )

        mgr.save_session(
            sid,
            history=[],
            model_name="m",
            message_nodes=nodes,
            active_leaf_id=branch_leaf,
            next_message_seq=next_seq + 1,
        )
        data = mgr.load_session(sid)

        assert len(data["message_nodes"]) == 3
        assert data["active_leaf_id"] == branch_leaf
        assert data["history"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "branch"},
        ]

    def test_save_is_atomic_via_tmp_file(self, mgr):
        sid = mgr.start_new_session()
        mgr.save_session(
            sid,
            history=[],
            model_name="m",
        )
        # tmp file should not exist after successful save
        tmp_path = mgr._session_path(sid) + ".tmp"
        assert not os.path.exists(tmp_path)

    def test_load_nonexistent_raises(self, mgr):
        with pytest.raises(FileNotFoundError):
            mgr.load_session("nonexistent")


class TestListSessions:
    def test_empty_directory_returns_empty_list(self, mgr):
        assert mgr.list_sessions() == []

    def test_list_sorted_by_last_active_desc(self, mgr):
        sid1 = mgr.start_new_session()
        sid2 = mgr.start_new_session()
        mgr.save_session(sid1, history=[], model_name="m1", name="First")
        mgr.save_session(sid2, history=[], model_name="m2", name="Second")
        sessions = mgr.list_sessions()
        assert len(sessions) == 2
        assert sessions[0]["id"] == sid2
        assert sessions[1]["id"] == sid1

    def test_list_excludes_non_json_files(self, mgr):
        # Create a stray file
        with open(os.path.join(mgr.sessions_dir, "readme.txt"), "w") as f:
            f.write("not a session")
        assert mgr.list_sessions() == []

    def test_list_skips_invalid_json(self, mgr):
        path = os.path.join(mgr.sessions_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{not json")
        sessions = mgr.list_sessions()
        assert all(s["id"] != "bad" for s in sessions)

    def test_list_returns_expected_fields(self, mgr):
        sid = mgr.start_new_session()
        mgr.save_session(sid, history=[], model_name="m", name="Named")
        sessions = mgr.list_sessions()
        entry = sessions[0]
        for key in ("id", "name", "model_name", "created_at", "last_active_at"):
            assert key in entry


class TestDelete:
    def test_delete_removes_file(self, mgr):
        sid = mgr.start_new_session()
        mgr.save_session(sid, history=[], model_name="m")
        mgr.delete_session(sid)
        assert mgr.list_sessions() == []

    def test_delete_nonexistent_no_error(self, mgr):
        mgr.delete_session("nonexistent")


class TestGetSessionName:
    def test_returns_name(self, mgr):
        sid = mgr.start_new_session()
        mgr.save_session(sid, history=[], model_name="m", name="My Session")
        assert mgr.get_session_name(sid) == "My Session"

    def test_returns_empty_for_missing(self, mgr):
        assert mgr.get_session_name("ghost") == ""
