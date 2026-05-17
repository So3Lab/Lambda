"""Session persistence and management."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any


class SessionManager:
    """Manages session files in {workspace}/.lambda/sessions/."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.sessions_dir = os.path.join(workspace, ".lambda", "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self.sessions_dir, f"{session_id}.json")

    def start_new_session(self) -> str:
        """Generate a new session UUID."""
        return str(uuid.uuid4())

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
    ) -> None:
        """Save session data atomically (write to .tmp then rename)."""
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "id": session_id,
            "name": name,
            "created_at": created_at or now,
            "last_active_at": now,
            "model_name": model_name,
            "provider_id": provider_id,
            "last_ctx_usage": last_ctx_usage,
            "history": history,
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
        """Load and return session data."""
        path = self._session_path(session_id)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

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
