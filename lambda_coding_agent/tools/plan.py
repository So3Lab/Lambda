"""Plan storage and tool functions for file-backed plan management.

All operations are scoped to <workspace>/.lambda/plans/.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

PLAN_STATUS_VALUES = ("draft", "active", "completed", "failed", "abandoned")
TASK_STATUS_VALUES = ("pending", "in_progress", "completed", "failed", "blocked", "skipped")
EXECUTION_MODE_VALUES = ("main_agent", "fork_candidate", "forked")


class PlanManager:
    """Manages plan files in {workspace}/.lambda/plans/.

    Plan indices are session-scoped when *session_id* is provided.
    Each session gets its own index at ``.lambda/plans/sessions/{session_id}.json``.
    When *session_id* is ``None``, falls back to the global ``index.json``
    (for backwards compatibility).
    """

    def __init__(self, workspace: str, session_id: str | None = None) -> None:
        self.workspace = workspace
        self.session_id = session_id
        self.plans_dir = os.path.join(workspace, ".lambda", "plans")
        os.makedirs(self.plans_dir, exist_ok=True)
        if session_id is not None:
            self._sessions_dir = os.path.join(self.plans_dir, "sessions")
            os.makedirs(self._sessions_dir, exist_ok=True)

    # ── Index management ───────────────────────────────────────

    @property
    def index_path(self) -> str:
        if self.session_id is not None:
            return os.path.join(self._sessions_dir, f"{self.session_id}.json")
        return os.path.join(self.plans_dir, "index.json")

    def _load_index(self) -> dict[str, Any]:
        if not os.path.exists(self.index_path):
            return {"version": 1, "active_plan_id": None, "plans": []}
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, index: dict[str, Any]) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_path)

    def get_active_plan_id(self) -> str | None:
        return self._load_index().get("active_plan_id")

    def set_active_plan_id(self, plan_id: str) -> None:
        index = self._load_index()
        for entry in index["plans"]:
            if entry["id"] == plan_id:
                entry["status"] = "active"
                break
        index["active_plan_id"] = plan_id
        self._save_index(index)

    def deactivate_all_plans(self) -> None:
        index = self._load_index()
        for entry in index["plans"]:
            if entry["status"] == "active":
                entry["status"] = "completed"
        index["active_plan_id"] = None
        self._save_index(index)

    # ── Plan file management ───────────────────────────────────

    def _plan_path(self, plan_id: str) -> str:
        return os.path.join(self.plans_dir, f"{plan_id}.json")

    def _resolve_plan_path(self, plan_id: str) -> str:
        """Resolve plan_id to absolute path. Reject path traversal."""
        if plan_id == "current":
            active = self.get_active_plan_id()
            if active is None:
                raise ValueError("No active plan. Create one with plan_create first.")
            plan_id = active
        if os.sep in plan_id or "/" in plan_id:
            raise ValueError("Invalid plan_id: must be a plain identifier, not a path")
        path = self._plan_path(plan_id)
        norm = os.path.normpath(path)
        if not norm.startswith(os.path.normpath(self.plans_dir)):
            raise ValueError("Plan path is outside workspace.")
        return norm

    def create_plan(
        self,
        title: str,
        goal: str,
        tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        plan_id = f"plan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        plan_tasks = []
        if tasks:
            for i, t in enumerate(tasks, start=1):
                task_id = f"task_{i:03d}"
                plan_tasks.append({
                    "id": task_id,
                    "title": t.get("title", ""),
                    "description": t.get("description", ""),
                    "status": "pending",
                    "depends_on": t.get("depends_on", []),
                    "execution_mode": t.get("execution_mode", "main_agent"),
                    "fork_id": None,
                    "result": None,
                    "error": None,
                })

        plan = {
            "version": 1,
            "id": plan_id,
            "title": title,
            "goal": goal,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "revision": 1,
            "tasks": plan_tasks,
        }

        self._validate_plan(plan)

        path = self._plan_path(plan_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

        index = self._load_index()
        index["plans"].append({
            "id": plan_id,
            "title": title,
            "path": f".lambda/plans/{plan_id}.json",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        })
        for entry in index["plans"]:
            if entry["id"] != plan_id and entry["status"] == "active":
                entry["status"] = "completed"
        index["active_plan_id"] = plan_id
        self._save_index(index)

        return plan

    def load_plan(self, plan_id: str = "current") -> dict[str, Any]:
        path = self._resolve_plan_path(plan_id)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_plan(self, plan: dict[str, Any]) -> None:
        plan_id = plan["id"]
        path = self._plan_path(plan_id)
        self._validate_plan(plan)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

        index = self._load_index()
        for entry in index["plans"]:
            if entry["id"] == plan_id:
                entry["updated_at"] = plan["updated_at"]
                entry["status"] = plan["status"]
                break
        self._save_index(index)

    def list_plans(self, status: str | None = None) -> list[dict[str, Any]]:
        index = self._load_index()
        plans = index.get("plans", [])
        if status:
            plans = [p for p in plans if p.get("status") == status]
        return plans

    # ── Task operations ────────────────────────────────────────

    def update_task(
        self,
        plan_id: str,
        task_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        plan = self.load_plan(plan_id)
        task = None
        for t in plan["tasks"]:
            if t["id"] == task_id:
                task = t
                break
        if task is None:
            raise ValueError(f"Task {task_id} not found in plan {plan['id']}")

        allowed_keys = {"status", "execution_mode", "fork_id", "result", "error"}
        for key, value in updates.items():
            if key in allowed_keys:
                task[key] = value

        now = datetime.now(timezone.utc).isoformat()
        if task["status"] in ("completed", "failed", "skipped"):
            task["completed_at"] = now
        if task["status"] == "in_progress" and not task.get("started_at"):
            task["started_at"] = now

        plan["updated_at"] = now
        plan["revision"] += 1
        self.save_plan(plan)

        # Auto-deactivate if all non-skipped tasks are terminal
        terminal = {"completed", "failed", "skipped"}
        non_skipped = [t for t in plan["tasks"] if t.get("status") != "skipped"]
        if non_skipped and all(t.get("status") in terminal for t in non_skipped):
            plan["status"] = "completed"
            plan["updated_at"] = datetime.now(timezone.utc).isoformat()
            plan["revision"] += 1
            self.save_plan(plan)
            self.deactivate_all_plans()

        return plan

    def add_tasks(
        self,
        plan_id: str,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        plan = self.load_plan(plan_id)
        existing_ids = {t["id"] for t in plan["tasks"]}
        next_num = max(
            (int(t["id"].split("_")[1]) for t in plan["tasks"]),
            default=0,
        ) + 1

        for _ in tasks:
            task_id = f"task_{next_num:03d}"
            while task_id in existing_ids:
                next_num += 1
                task_id = f"task_{next_num:03d}"
            existing_ids.add(task_id)
            t = tasks[next_num - 1 - (int(task_id.split("_")[1]) - 1)]
            plan["tasks"].append({
                "id": task_id,
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "status": "pending",
                "depends_on": t.get("depends_on", []),
                "execution_mode": t.get("execution_mode", "main_agent"),
                "fork_id": None,
                "result": None,
                "error": None,
            })
            next_num += 1

        plan["updated_at"] = datetime.now(timezone.utc).isoformat()
        plan["revision"] += 1
        self.save_plan(plan)
        return plan

    # ── Summary computation ────────────────────────────────────

    def compute_summary(self, plan: dict[str, Any], view: str = "summary") -> str:
        tasks = plan.get("tasks", [])
        counts: dict[str, int] = {}
        for t in tasks:
            s = t.get("status", "pending")
            counts[s] = counts.get(s, 0) + 1

        if view == "summary":
            lines = [
                f"Plan: {plan['title']}",
                f"Status: {plan['status']}",
                f"Path: .lambda/plans/{plan['id']}.json",
            ]
            parts = []
            for s in ("completed", "in_progress", "pending", "failed", "blocked", "skipped"):
                if s in counts:
                    parts.append(f"{counts[s]} {s}")
            lines.append(f"Progress: {', '.join(parts)}")

            ready_main = self._ready_tasks(plan, "main_agent")
            if ready_main:
                lines.append(f"Ready main-agent tasks: {', '.join(ready_main)}")

            ready_forks = self._ready_tasks(plan, "fork_candidate")
            if ready_forks:
                lines.append(f"Ready fork candidates: {', '.join(ready_forks)}")

            bg_forks = [
                f"{t['id']}={t['fork_id']}"
                for t in tasks
                if t.get("status") == "in_progress"
                and t.get("execution_mode") == "forked"
                and t.get("fork_id")
            ]
            if bg_forks:
                lines.append(f"Background forks: {', '.join(bg_forks)}")

            return "\n".join(lines)

        elif view == "ready":
            ready_main = self._ready_tasks(plan, "main_agent")
            ready_forks = self._ready_tasks(plan, "fork_candidate")
            lines = []
            if ready_main:
                lines.append("Ready main-agent tasks:")
                for tid in ready_main:
                    task = next(t for t in tasks if t["id"] == tid)
                    lines.append(f"  {tid}: {task['title']}")
            if ready_forks:
                lines.append("Ready fork candidates:")
                for tid in ready_forks:
                    task = next(t for t in tasks if t["id"] == tid)
                    lines.append(f"  {tid}: {task['title']}")
            if not lines:
                lines.append("No ready tasks.")
            return "\n".join(lines)

        elif view == "full":
            return json.dumps(plan, ensure_ascii=False, indent=2)

        return f"Unknown view: {view}"

    def _ready_tasks(self, plan: dict[str, Any], mode: str) -> list[str]:
        completed = {t["id"] for t in plan["tasks"] if t["status"] == "completed"}
        ready = []
        for t in plan["tasks"]:
            if t["status"] != "pending":
                continue
            if t.get("execution_mode") != mode:
                continue
            deps = set(t.get("depends_on", []))
            if deps.issubset(completed):
                ready.append(t["id"])
        return ready

    # ── Validation ─────────────────────────────────────────────

    def _validate_plan(self, plan: dict[str, Any]) -> None:
        if not plan.get("title"):
            raise ValueError("Plan title must be non-empty.")
        if not plan.get("goal"):
            raise ValueError("Plan goal must be non-empty.")
        if plan.get("status") not in PLAN_STATUS_VALUES:
            raise ValueError(f"Invalid plan status: {plan.get('status')}")

        all_task_ids = {t["id"] for t in plan.get("tasks", [])}
        for t in plan.get("tasks", []):
            if not t.get("title"):
                raise ValueError(f"Task {t.get('id', '?')} title must be non-empty.")
            if t.get("status") not in TASK_STATUS_VALUES:
                raise ValueError(f"Invalid task status: {t.get('status')}")
            if t.get("execution_mode") not in EXECUTION_MODE_VALUES:
                raise ValueError(f"Invalid execution mode: {t.get('execution_mode')}")

            tid = t["id"]
            deps = t.get("depends_on", [])
            if tid in deps:
                raise ValueError(f"Task {tid} cannot depend on itself.")
            for dep in deps:
                if dep not in all_task_ids:
                    raise ValueError(f"Task {tid} depends on unknown task: {dep}")

        self._check_dependency_cycles(plan.get("tasks", []))

    def _check_dependency_cycles(self, tasks: list[dict[str, Any]]) -> None:
        graph = {t["id"]: set(t.get("depends_on", [])) for t in tasks}
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for dep in graph.get(node, []):
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for task_id in graph:
            if task_id not in visited:
                if has_cycle(task_id):
                    raise ValueError("Dependency cycle detected in plan tasks.")


# ═══════════════════════════════════════════════════════════════════════
# Tool functions (wrapped with @tool() in agent.py)
# ═══════════════════════════════════════════════════════════════════════


async def plan_create(
    title: str,
    goal: str,
    tasks: list[dict[str, Any]] | None = None,
    workspace: str = "",
) -> str:
    """Create a new file-backed plan and set it as active."""
    mgr = PlanManager(workspace)
    plan = mgr.create_plan(title, goal, tasks)
    n = len(plan["tasks"])
    return (
        f"Created active plan {plan['id']} with {n} task(s).\n"
        f"Saved to .lambda/plans/{plan['id']}.json"
    )


async def plan_get(
    plan_id: str = "current",
    view: str = "summary",
    workspace: str = "",
) -> str:
    """Read a plan from disk."""
    mgr = PlanManager(workspace)
    plan = mgr.load_plan(plan_id)
    return mgr.compute_summary(plan, view)


async def plan_update_task(
    plan_id: str = "current",
    task_id: str = "",
    status: str | None = None,
    execution_mode: str | None = None,
    fork_id: str | None = None,
    result: str | None = None,
    error: str | None = None,
    workspace: str = "",
) -> str:
    """Update a single task in the plan."""
    mgr = PlanManager(workspace)
    updates: dict[str, Any] = {}
    for key, val in [
        ("status", status),
        ("execution_mode", execution_mode),
        ("fork_id", fork_id),
        ("result", result),
        ("error", error),
    ]:
        if val is not None:
            updates[key] = val
    plan = mgr.update_task(plan_id, task_id, updates)

    counts: dict[str, int] = {}
    for t in plan["tasks"]:
        s = t.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
    parts = []
    for s in ("completed", "in_progress", "pending"):
        if s in counts:
            parts.append(f"{counts[s]} {s}")
    progress = ", ".join(parts)

    return (
        f"Updated {task_id} to {status or 'unchanged'}.\n"
        f"Plan saved to .lambda/plans/{plan['id']}.json.\n"
        f"Progress: {progress}."
    )


async def plan_add_tasks(
    plan_id: str = "current",
    tasks: list[dict[str, Any]] | None = None,
    workspace: str = "",
) -> str:
    """Add new tasks to an existing plan."""
    if tasks is None:
        tasks = []
    mgr = PlanManager(workspace)
    plan = mgr.add_tasks(plan_id, tasks)
    return (
        f"Added {len(tasks)} task(s) to {plan['id']}.\n"
        f"Plan saved to .lambda/plans/{plan['id']}.json"
    )
