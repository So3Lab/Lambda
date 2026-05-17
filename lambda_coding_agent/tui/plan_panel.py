"""Persistent plan panel widget for the TUI.

Renders a compact, always-visible plan status bar when an active plan exists.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Static

from lambda_coding_agent.tools.plan import PlanManager

_TASK_ICONS = {
    "completed": "\u2713",    # ✓
    "in_progress": "~",
    "pending": " ",
    "failed": "x",
    "blocked": "!",
    "skipped": "-",
    "forked": "\u21c4",      # ⇄
}

_TASK_COLORS = {
    "completed": "#5f8d5a",
    "in_progress": "#d4a373",
    "failed": "#c0392b",
    "blocked": "#d4a373",
    "forked": "#6f87a8",
}


class PlanPanel(Vertical):
    """A compact panel showing the active plan's task list.

    Renders at the top of the main chat area when a plan is active.
    """

    DEFAULT_CSS = """
    PlanPanel {
        height: auto;
        margin: 0 0 0 0;
        padding: 0 1;
        border: solid $primary 30%;
    }
    .plan-panel-header {
        text-style: bold;
        padding: 0 1;
    }
    .plan-task-list {
        padding: 0 1 1 1;
    }
    """

    def __init__(self, plan_manager: PlanManager, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.plan_manager = plan_manager
        self._plan_id: str | None = None

    def compose(self):
        yield Static("Plan: loading...", classes="plan-panel-header", markup=True)
        yield Static("", classes="plan-task-list", markup=False)

    def on_mount(self) -> None:
        self.refresh_if_active()

    def refresh_if_active(self) -> None:
        """Reload plan from disk and re-render if an active plan exists."""
        plan_id = self.plan_manager.get_active_plan_id()
        if plan_id is None:
            self.display = False
            self._plan_id = None
            return

        try:
            plan = self.plan_manager.load_plan(plan_id)
        except (FileNotFoundError, ValueError):
            self.display = False
            self._plan_id = None
            return

        self._plan_id = plan_id
        self.display = True

        header = f"Plan: {plan['title']} ({plan['status']})"
        try:
            self.query_one(".plan-panel-header", Static).update(header)
        except Exception:
            pass

        tasks = plan.get("tasks", [])
        if not tasks:
            try:
                self.query_one(".plan-task-list", Static).update("No tasks yet.")
            except Exception:
                pass
            return

        # Build Rich Text with per-line colors
        rich_lines = Text()
        for i, task in enumerate(tasks):
            status = task.get("status", "pending")
            icon = _TASK_ICONS.get(status, " ")
            fork_suffix = f" [{task['fork_id']}]" if task.get("fork_id") else ""
            line = f"{icon} {task['id']}  {task['title']}{fork_suffix}"
            color = _TASK_COLORS.get(status, "#9ba3b0")
            rich_lines.append(line + "\n", style=color)

        try:
            task_widget = self.query_one(".plan-task-list", Static)
            task_widget.update(rich_lines)
        except Exception:
            pass
