"""Tests for plan storage and tool functions."""

import json
import os
import tempfile

import pytest

from lambda_coding_agent.tools.plan import (
    PlanManager,
    plan_create,
    plan_get,
    plan_update_task,
    plan_add_tasks,
)


@pytest.fixture()
def tmp_workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture()
def mgr(tmp_workspace):
    return PlanManager(tmp_workspace)


class TestPlanManager:
    def test_creates_plans_dir(self, mgr):
        assert os.path.isdir(mgr.plans_dir)

    def test_index_created_on_first_write(self, mgr):
        mgr.create_plan("Test", "Goal")
        assert os.path.exists(mgr.index_path)

    def test_create_plan_returns_valid_structure(self, mgr):
        plan = mgr.create_plan(
            "Test Plan",
            "Achieve something",
            tasks=[
                {"title": "Task 1", "description": "Do thing 1"},
                {"title": "Task 2", "description": "Do thing 2", "execution_mode": "fork_candidate"},
            ],
        )
        assert plan["id"].startswith("plan_")
        assert plan["status"] == "active"
        assert len(plan["tasks"]) == 2
        assert plan["tasks"][0]["id"] == "task_001"
        assert plan["tasks"][1]["id"] == "task_002"

    def test_active_plan_tracking(self, mgr):
        mgr.create_plan("First", "Goal 1")
        mgr.create_plan("Second", "Goal 2")
        assert mgr.get_active_plan_id() is not None
        plan = mgr.load_plan("current")
        assert plan["title"] == "Second"

    def test_load_plan_by_id(self, mgr):
        plan = mgr.create_plan("Unique", "Goal")
        loaded = mgr.load_plan(plan["id"])
        assert loaded["title"] == "Unique"

    def test_update_task_status(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[{"title": "T1"}])
        updated = mgr.update_task(plan["id"], "task_001", {"status": "in_progress"})
        task = updated["tasks"][0]
        assert task["status"] == "in_progress"
        assert updated["revision"] == 2

    def test_update_task_not_found(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[{"title": "T1"}])
        with pytest.raises(ValueError, match="not found"):
            mgr.update_task(plan["id"], "task_999", {"status": "completed"})

    def test_add_tasks(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[{"title": "T1"}])
        updated = mgr.add_tasks(plan["id"], [{"title": "T2"}, {"title": "T3"}])
        assert len(updated["tasks"]) == 3
        assert updated["tasks"][2]["id"] == "task_003"

    def test_compute_summary(self, mgr):
        plan = mgr.create_plan(
            "Test",
            "Goal",
            tasks=[
                {"title": "Done", "execution_mode": "main_agent"},
                {"title": "Running", "execution_mode": "main_agent"},
                {"title": "Waiting", "execution_mode": "main_agent"},
            ],
        )
        plan = mgr.update_task(plan["id"], "task_001", {"status": "completed"})
        plan = mgr.update_task(plan["id"], "task_002", {"status": "in_progress"})
        summary = mgr.compute_summary(plan, "summary")
        assert "1 completed" in summary
        assert "1 in_progress" in summary
        assert "1 pending" in summary

    def test_ready_tasks_computation(self, mgr):
        plan = mgr.create_plan(
            "Test",
            "Goal",
            tasks=[
                {"title": "T1", "execution_mode": "main_agent"},
                {"title": "T2", "depends_on": ["task_001"], "execution_mode": "main_agent"},
                {"title": "T3", "execution_mode": "fork_candidate"},
            ],
        )
        ready = mgr._ready_tasks(plan, "main_agent")
        assert "task_001" in ready
        assert "task_002" not in ready  # blocked by task_001
        ready_forks = mgr._ready_tasks(plan, "fork_candidate")
        assert "task_003" in ready_forks

    def test_reject_invalid_task_status(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[{"title": "T1"}])
        with pytest.raises(ValueError, match="Invalid task status"):
            mgr.update_task(plan["id"], "task_001", {"status": "invalid"})

    def test_reject_self_dependency(self, mgr):
        with pytest.raises(ValueError, match="cannot depend on itself"):
            mgr.create_plan("Test", "Goal", tasks=[
                {"title": "T1", "depends_on": ["task_001"]},
            ])

    def test_reject_missing_dependency(self, mgr):
        with pytest.raises(ValueError, match="unknown task"):
            mgr.create_plan("Test", "Goal", tasks=[
                {"title": "T1", "depends_on": ["task_999"]},
            ])

    def test_reject_dependency_cycle(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[
            {"title": "T1"},
            {"title": "T2"},
        ])
        plan["tasks"][0]["depends_on"] = ["task_002"]
        plan["tasks"][1]["depends_on"] = ["task_001"]
        with pytest.raises(ValueError, match="cycle"):
            mgr.save_plan(plan)

    def test_path_traversal_rejected(self, mgr):
        with pytest.raises(ValueError):
            mgr._resolve_plan_path("../../etc/passwd")

    def test_path_traversal_with_slash_rejected(self, mgr):
        with pytest.raises(ValueError):
            mgr._resolve_plan_path("foo/bar")

    def test_atomic_write_no_tmp_left(self, mgr):
        plan = mgr.create_plan("Test", "Goal")
        path = mgr._plan_path(plan["id"])
        assert not os.path.exists(path + ".tmp")

    def test_list_plans(self, mgr):
        mgr.create_plan("Active", "G1")
        mgr.create_plan("Second", "G2")
        all_plans = mgr.list_plans()
        assert len(all_plans) == 2
        active = mgr.list_plans(status="active")
        assert len(active) == 1
        assert active[0]["title"] == "Second"

    def test_deactivate_all_plans(self, mgr):
        mgr.create_plan("First", "G1")
        mgr.deactivate_all_plans()
        assert mgr.get_active_plan_id() is None

    def test_auto_deactivate_on_all_tasks_completed(self, mgr):
        """When all non-skipped tasks are terminal, plan auto-deactivates."""
        plan = mgr.create_plan("Test", "Goal", tasks=[
            {"title": "T1"},
            {"title": "T2"},
        ])
        # Complete all tasks
        mgr.update_task(plan["id"], "task_001", {"status": "completed"})
        mgr.update_task(plan["id"], "task_002", {"status": "completed"})
        # Plan should be deactivated
        assert mgr.get_active_plan_id() is None
        # Reload and verify status
        reloaded = mgr.load_plan(plan["id"])
        assert reloaded["status"] == "completed"

    def test_auto_deactivate_skipped_not_counted(self, mgr):
        """Skipped tasks don't block auto-deactivation."""
        plan = mgr.create_plan("Test", "Goal", tasks=[
            {"title": "T1"},
            {"title": "T2"},
            {"title": "T3"},
        ])
        mgr.update_task(plan["id"], "task_001", {"status": "completed"})
        mgr.update_task(plan["id"], "task_002", {"status": "skipped"})
        mgr.update_task(plan["id"], "task_003", {"status": "completed"})
        # T2 is skipped, T1 and T3 completed -> all non-skipped terminal
        assert mgr.get_active_plan_id() is None

    def test_no_active_plan_raises_on_current(self, mgr):
        with pytest.raises(ValueError, match="No active plan"):
            mgr.load_plan("current")

    def test_compute_summary_ready_view(self, mgr):
        plan = mgr.create_plan(
            "Test",
            "Goal",
            tasks=[
                {"title": "T1", "execution_mode": "main_agent"},
                {"title": "T2", "depends_on": ["task_001"], "execution_mode": "main_agent"},
            ],
        )
        summary = mgr.compute_summary(plan, "ready")
        assert "Ready main-agent tasks:" in summary
        assert "task_001" in summary
        assert "task_002" not in summary

    def test_compute_summary_full_view(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[{"title": "T1"}])
        summary = mgr.compute_summary(plan, "full")
        data = json.loads(summary)
        assert data["title"] == "Test"

    def test_empty_title_rejected(self, mgr):
        with pytest.raises(ValueError, match="title must be non-empty"):
            mgr.create_plan("", "Goal")

    def test_empty_goal_rejected(self, mgr):
        with pytest.raises(ValueError, match="goal must be non-empty"):
            mgr.create_plan("Test", "")

    def test_invalid_plan_status_rejected(self, mgr):
        plan = mgr.create_plan("Test", "Goal")
        plan["status"] = "bogus"
        with pytest.raises(ValueError, match="Invalid plan status"):
            mgr.save_plan(plan)

    def test_invalid_execution_mode_rejected(self, mgr):
        plan = mgr.create_plan("Test", "Goal", tasks=[{"title": "T1"}])
        plan["tasks"][0]["execution_mode"] = "bogus"
        with pytest.raises(ValueError, match="Invalid execution mode"):
            mgr.save_plan(plan)


class TestPlanSessionIsolation:
    """Plans are scoped to a session — different sessions have independent active plans."""

    def test_two_sessions_independent_active_plans(self, tmp_path):
        """Two PlanManagers with different session_ids track separate active plans."""
        mgr_a = PlanManager(str(tmp_path), session_id="session_a")
        mgr_b = PlanManager(str(tmp_path), session_id="session_b")

        plan_a = mgr_a.create_plan("Plan A", "Goal A")
        plan_b = mgr_b.create_plan("Plan B", "Goal B")

        # Each session sees its own active plan
        assert mgr_a.get_active_plan_id() == plan_a["id"]
        assert mgr_b.get_active_plan_id() == plan_b["id"]

        # Deactivating session A doesn't affect session B
        mgr_a.deactivate_all_plans()
        assert mgr_a.get_active_plan_id() is None
        assert mgr_b.get_active_plan_id() == plan_b["id"]

    def test_session_auto_deactivate_independent(self, tmp_path):
        """Auto-deactivate on task completion only affects the owning session."""
        mgr_a = PlanManager(str(tmp_path), session_id="session_a")
        mgr_b = PlanManager(str(tmp_path), session_id="session_b")

        plan_a = mgr_a.create_plan("Plan A", "Goal A", tasks=[{"title": "T1"}])
        mgr_b.create_plan("Plan B", "Goal B", tasks=[{"title": "T1"}])

        # Complete all tasks in session A — triggers auto-deactivate
        mgr_a.update_task(plan_a["id"], "task_001", {"status": "completed"})
        assert mgr_a.get_active_plan_id() is None
        # Session B should still have its active plan
        assert mgr_b.get_active_plan_id() is not None

    def test_fork_inherits_parent_plan(self, tmp_path):
        """Copying a session's plan index to a fork preserves the active plan."""
        import shutil

        mgr_parent = PlanManager(str(tmp_path), session_id="parent")
        plan = mgr_parent.create_plan("Parent Plan", "Goal", tasks=[{"title": "T1"}])

        # Simulate fork: copy parent index to child
        fork_id = "fork_child"
        parent_index = mgr_parent.index_path
        fork_index = os.path.join(mgr_parent.plans_dir, "sessions", f"{fork_id}.json")
        os.makedirs(os.path.dirname(fork_index), exist_ok=True)
        shutil.copy2(parent_index, fork_index)

        mgr_fork = PlanManager(str(tmp_path), session_id=fork_id)
        assert mgr_fork.get_active_plan_id() == plan["id"]
        # Verify the fork can load the plan
        loaded = mgr_fork.load_plan(plan["id"])
        assert loaded["title"] == "Parent Plan"

    def test_no_session_id_uses_global_index(self, tmp_path):
        """PlanManager without session_id falls back to global index.json."""
        mgr_global = PlanManager(str(tmp_path))
        mgr_session = PlanManager(str(tmp_path), session_id="s1")

        plan = mgr_global.create_plan("Global", "Goal")
        assert mgr_global.get_active_plan_id() == plan["id"]
        # Session-scoped manager doesn't see global active plan
        assert mgr_session.get_active_plan_id() is None


class TestPlanTools:
    @pytest.mark.asyncio
    async def test_plan_create_tool(self, tmp_workspace):
        result = await plan_create(
            title="Test Plan",
            goal="Test goal",
            tasks=[{"title": "T1"}],
            workspace=tmp_workspace,
        )
        assert "Created active plan" in result
        assert ".lambda/plans/" in result

    @pytest.mark.asyncio
    async def test_plan_get_tool(self, tmp_workspace):
        await plan_create(title="Test", goal="Goal", workspace=tmp_workspace)
        result = await plan_get(plan_id="current", view="summary", workspace=tmp_workspace)
        assert "Plan: Test" in result

    @pytest.mark.asyncio
    async def test_plan_update_task_tool(self, tmp_workspace):
        await plan_create(
            title="Test",
            goal="Goal",
            tasks=[{"title": "T1"}],
            workspace=tmp_workspace,
        )
        result = await plan_update_task(
            plan_id="current",
            task_id="task_001",
            status="completed",
            workspace=tmp_workspace,
        )
        assert "Updated task_001 to completed" in result

    @pytest.mark.asyncio
    async def test_plan_add_tasks_tool(self, tmp_workspace):
        await plan_create(title="Test", goal="Goal", workspace=tmp_workspace)
        result = await plan_add_tasks(
            plan_id="current",
            tasks=[{"title": "New task"}],
            workspace=tmp_workspace,
        )
        assert "Added 1 task" in result

    @pytest.mark.asyncio
    async def test_plan_get_no_active_plan(self, tmp_workspace):
        with pytest.raises(ValueError, match="No active plan"):
            await plan_get(plan_id="current", workspace=tmp_workspace)

    @pytest.mark.asyncio
    async def test_plan_create_with_no_tasks(self, tmp_workspace):
        result = await plan_create(
            title="Minimal",
            goal="Just a goal",
            workspace=tmp_workspace,
        )
        assert "0 task" in result

    @pytest.mark.asyncio
    async def test_plan_add_tasks_with_none(self, tmp_workspace):
        await plan_create(title="Test", goal="Goal", workspace=tmp_workspace)
        result = await plan_add_tasks(plan_id="current", tasks=None, workspace=tmp_workspace)
        assert "Added 0 task" in result
