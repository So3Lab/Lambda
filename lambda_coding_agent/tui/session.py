"""Session persistence and management."""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any


class SessionManager:
    """Manages session files in {workspace}/.lambda/sessions/."""

    SCHEMA_VERSION = 2

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.sessions_dir = os.path.join(workspace, ".lambda", "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self.sessions_dir, f"{session_id}.json")

    def start_new_session(self) -> str:
        """Generate a new session UUID."""
        return str(uuid.uuid4())

    @staticmethod
    def _new_message_id(next_message_seq: int) -> str:
        return f"msg_{next_message_seq:06d}"

    @classmethod
    def _next_message_seq_from_nodes(cls, message_nodes: dict[str, Any]) -> int:
        max_seq = 0
        for node_id in message_nodes:
            if not node_id.startswith("msg_"):
                continue
            try:
                max_seq = max(max_seq, int(node_id[4:]))
            except ValueError:
                continue
        return max_seq + 1 if max_seq else len(message_nodes) + 1

    @classmethod
    def history_to_message_tree(
        cls,
        history: list[dict[str, Any]],
        *,
        next_message_seq: int = 1,
    ) -> tuple[dict[str, dict[str, Any]], str | None, int]:
        """Convert a linear history into one trunk branch."""
        nodes: dict[str, dict[str, Any]] = {}
        parent_id: str | None = None
        seq = next_message_seq
        now = datetime.now(timezone.utc).isoformat()
        for message in history:
            node_id = cls._new_message_id(seq)
            seq += 1
            nodes[node_id] = {
                "id": node_id,
                "parent_id": parent_id,
                "created_at": now,
                "message": copy.deepcopy(message),
            }
            parent_id = node_id
        return nodes, parent_id, seq

    @staticmethod
    def project_active_history(
        message_nodes: dict[str, Any],
        active_leaf_id: str | None,
    ) -> list[dict[str, Any]]:
        """Project the active root-to-leaf branch into a linear history."""
        path: list[dict[str, Any]] = []
        seen: set[str] = set()
        current_id = active_leaf_id
        while current_id:
            if current_id in seen:
                break
            seen.add(current_id)
            node = message_nodes.get(current_id)
            if not isinstance(node, dict):
                break
            message = node.get("message", {})
            if isinstance(message, dict):
                path.append(copy.deepcopy(message))
            current_id = node.get("parent_id")
        path.reverse()
        return path

    @staticmethod
    def active_path_ids(
        message_nodes: dict[str, Any],
        active_leaf_id: str | None,
    ) -> list[str]:
        """Return node ids on the active root-to-leaf path."""
        return SessionManager.path_ids_to_node(message_nodes, active_leaf_id)

    @staticmethod
    def path_ids_to_node(
        message_nodes: dict[str, Any],
        node_id: str | None,
    ) -> list[str]:
        """Return node ids on the root-to-node path."""
        path: list[str] = []
        seen: set[str] = set()
        current_id = node_id
        while current_id:
            if current_id in seen:
                break
            seen.add(current_id)
            node = message_nodes.get(current_id)
            if not isinstance(node, dict):
                break
            path.append(current_id)
            current_id = node.get("parent_id")
        path.reverse()
        return path

    @staticmethod
    def parent_id_for_node(
        message_nodes: dict[str, Any],
        node_id: str | None,
    ) -> str | None:
        """Return the parent id for a message node, if present."""
        node = message_nodes.get(node_id) if node_id else None
        if not isinstance(node, dict):
            return None
        parent_id = node.get("parent_id")
        return parent_id if isinstance(parent_id, str) else None

    @classmethod
    def append_message_node(
        cls,
        message_nodes: dict[str, dict[str, Any]],
        parent_id: str | None,
        message: dict[str, Any],
        next_message_seq: int,
    ) -> tuple[str, int]:
        """Append a message node to a branch and return the new leaf and sequence."""
        node_id = cls._new_message_id(next_message_seq)
        while node_id in message_nodes:
            next_message_seq += 1
            node_id = cls._new_message_id(next_message_seq)
        message_nodes[node_id] = {
            "id": node_id,
            "parent_id": parent_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "message": copy.deepcopy(message),
        }
        return node_id, next_message_seq + 1

    def normalize_session_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return session data with v2 branch fields and active projected history."""
        normalized = dict(data)
        raw_nodes = normalized.get("message_nodes")
        if isinstance(raw_nodes, dict):
            nodes = copy.deepcopy(raw_nodes)
            active_leaf_id = normalized.get("active_leaf_id")
            if active_leaf_id not in nodes:
                active_leaf_id = None
            history = self.project_active_history(nodes, active_leaf_id)
            next_message_seq = normalized.get("next_message_seq")
            if not isinstance(next_message_seq, int) or next_message_seq < 1:
                next_message_seq = self._next_message_seq_from_nodes(nodes)
        else:
            raw_history = normalized.get("history", [])
            history = raw_history if isinstance(raw_history, list) else []
            nodes, active_leaf_id, next_message_seq = self.history_to_message_tree(history)

        normalized["schema_version"] = self.SCHEMA_VERSION
        normalized["message_nodes"] = nodes
        normalized["active_leaf_id"] = active_leaf_id
        normalized["next_message_seq"] = next_message_seq
        normalized["history"] = history
        return normalized

    def save_session(
        self,
        session_id: str,
        *,
        history: list[dict[str, Any]],
        model_name: str,
        name: str = "",
        provider_id: str = "",
        last_ctx_usage: int = 0,
        created_at: str | None = None,
        active_plan_id: str | None = None,
        active_plan_path: str | None = None,
        message_nodes: dict[str, dict[str, Any]] | None = None,
        active_leaf_id: str | None = None,
        next_message_seq: int | None = None,
    ) -> None:
        """Save session data atomically (write to .tmp then rename)."""
        now = datetime.now(timezone.utc).isoformat()
        if message_nodes is None:
            message_nodes, active_leaf_id, computed_seq = self.history_to_message_tree(history)
            next_message_seq = next_message_seq or computed_seq
        else:
            message_nodes = copy.deepcopy(message_nodes)
            if active_leaf_id not in message_nodes:
                active_leaf_id = None
            if next_message_seq is None:
                next_message_seq = self._next_message_seq_from_nodes(message_nodes)
            history = self.project_active_history(message_nodes, active_leaf_id)

        data = {
            "schema_version": self.SCHEMA_VERSION,
            "id": session_id,
            "name": name,
            "created_at": created_at or now,
            "last_active_at": now,
            "model_name": model_name,
            "provider_id": provider_id,
            "last_ctx_usage": last_ctx_usage,
            "history": history,
            "message_nodes": message_nodes,
            "active_leaf_id": active_leaf_id,
            "next_message_seq": next_message_seq,
        }
        if active_plan_id:
            data["active_plan_id"] = active_plan_id
        if active_plan_path:
            data["active_plan_path"] = active_plan_path
        path = self._session_path(session_id)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load_session(self, session_id: str) -> dict[str, Any]:
        """Load and return normalized session data."""
        path = self._session_path(session_id)
        with open(path, "r", encoding="utf-8") as f:
            return self.normalize_session_data(json.load(f))

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions sorted by last_active_at descending.

        Each entry: {id, name, model_name, created_at, last_active_at}
        """
        sessions: list[dict[str, Any]] = []
        for fname in os.listdir(self.sessions_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.sessions_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append({
                    "id": data.get("id", fname[:-5]),
                    "name": data.get("name", ""),
                    "model_name": data.get("model_name", "unknown"),
                    "created_at": data.get("created_at", ""),
                    "last_active_at": data.get("last_active_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue

        sessions.sort(
            key=lambda s: s["last_active_at"],
            reverse=True,
        )
        return sessions

    def delete_session(self, session_id: str) -> None:
        """Delete a session file."""
        path = self._session_path(session_id)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def get_session_name(self, session_id: str) -> str:
        """Get the name of a session."""
        try:
            data = self.load_session(session_id)
            return data.get("name", "")
        except (FileNotFoundError, json.JSONDecodeError):
            return ""
