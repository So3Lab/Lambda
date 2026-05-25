"""Workspace primitives for LambdaCodingAgent — CodeAct primitives installed into PyRepl.

All workspace operations (shell, file I/O, search, planning) are exposed as
runtime primitives callable via ``runtime.workspace.xxx(...)`` inside
``execute_code`` blocks.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from lambda_coding_agent.config import load_lambda_config
from lambda_coding_agent.tools.webfetch import _web_fetch_sync

from SimpleLLMFunc.runtime.primitives import (
    PrimitiveCallContext,
    PrimitivePack,
)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class WorkspaceBackend:
    """Shared state for all workspace primitives."""

    def __init__(self, workspace: str, session_id: str | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.session_id = session_id
        self.config = load_lambda_config(str(self.workspace))
        self.bypass_paths = self._load_bypass_paths()
        self._undo_stack: list[dict[str, Any]] = []
        self._plan_manager: Any = None  # lazy init

    @property
    def plan_manager(self) -> Any:
        if self._plan_manager is None:
            from lambda_coding_agent.tools.plan import PlanManager

            self._plan_manager = PlanManager(str(self.workspace), self.session_id)
        return self._plan_manager

    def _load_bypass_paths(self) -> list[Path]:
        """Load configured absolute paths that file primitives may access."""
        value = self.config.get("bypass_paths")
        if not isinstance(value, list):
            return []

        paths = [
            self._normalize_bypass_path(item)
            for item in value
            if isinstance(item, str)
        ]
        unique_paths: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique_paths.append(path)
        return unique_paths

    def _normalize_bypass_path(self, path: str) -> Path:
        if path == "~" or path.startswith("~/"):
            path = "$HOME" + path[1:]
        expanded = Path(os.path.expandvars(path)).expanduser()
        if not expanded.is_absolute():
            expanded = self.workspace / expanded
        return expanded.resolve(strict=False)

    def _format_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)

    def _is_allowed_path(self, path: Path) -> bool:
        try:
            path.relative_to(self.workspace)
            return True
        except ValueError:
            pass
        for bypass_path in self.bypass_paths:
            try:
                path.relative_to(bypass_path)
                return True
            except ValueError:
                continue
        return False

    def _resolve_path(self, path: str) -> tuple[Path | None, str | None]:
        """Resolve a path within the workspace or configured bypass paths. Returns (abs_path, error)."""
        try:
            raw_path = Path(os.path.expandvars(path)).expanduser()
            if raw_path.is_absolute():
                resolved = raw_path.resolve(strict=False)
            else:
                resolved = (self.workspace / raw_path).resolve(strict=False)
        except RuntimeError:
            return None, f"Path is outside workspace: {path}"

        if self._is_allowed_path(resolved):
            return resolved, None
        return None, f"Path is outside workspace and configured bypass paths: {path}"

    def _resolve_base_path(self, path: str) -> tuple[Path | None, str | None]:
        if not path:
            return self.workspace, None
        return self._resolve_path(path)


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------


def build_workspace_pack(workspace: str, session_id: str | None = None) -> PrimitivePack:
    """Create the workspace PrimitivePack."""
    backend = WorkspaceBackend(workspace, session_id)
    pack = PrimitivePack(
        "workspace",
        backend=backend,
        guidance="Workspace primitives for shell, file I/O, search, and planning. Call as runtime.workspace.xxx(...) inside execute_code.",
    )

    # ── Shell ──────────────────────────────────────────────────────

    @pack.primitive("run_command")
    async def ws_run_command(
        ctx: PrimitiveCallContext,
        command: str,
        cwd: str = "",
        timeout: int = 120,
    ) -> str:
        """
        Use: Run a shell command in the workspace.
        Input: `command: str`, `cwd: str` (optional, relative to workspace), `timeout: int` (default 120).
        Output: Structured text with exit_code, timed_out, duration_ms, stdout, stderr.
        Parse: Read exit_code first. If 0, read stdout. If non-zero, read stderr for error diagnosis.
        Parameters:
        - command: Shell command to execute.
        - cwd: Subdirectory relative to workspace root. Empty means workspace root.
        - timeout: Max seconds before killing the process.
        Best Practices:
        - Use for git operations, test runs, builds, linters, package managers.
        - Prefer quiet/silent flags (--quiet, -q, --silent) to reduce output noise.
        - Do NOT use for reading file contents — use read_file instead.
        - Do NOT use for file searching — use find_files or search instead.
        - Always check exit_code before trusting stdout. Non-zero means failure.
        - Ask user before destructive commands (rm, git reset, force push).
        - On timeout, command is killed — increase timeout or optimize command.
        Output Example:
          exit_code: 0
          timed_out: False
          duration_ms: 342
          truncated: False
          --- stdout ---
          All tests passed
          --- stderr ---
        """
        from lambda_coding_agent.tools.shell import run_command

        backend: WorkspaceBackend = ctx.backend
        relative_cwd: str | None = None
        if cwd:
            work_dir = backend.workspace / cwd
            try:
                resolved_work_dir = work_dir.resolve()
                relative_cwd = str(resolved_work_dir.relative_to(backend.workspace))
            except ValueError:
                return f"Directory not found: {cwd}"
            work_dir_str = str(resolved_work_dir)
        else:
            work_dir_str = str(backend.workspace)

        if not os.path.isdir(work_dir_str):
            return f"Directory not found: {work_dir_str}"

        result = await run_command(
            command,
            workspace=str(backend.workspace),
            cwd=relative_cwd,
            timeout=timeout,
        )

        lines = [
            f"exit_code: {result['exit_code']}",
            f"timed_out: {result['timed_out']}",
            f"duration_ms: {result['duration_ms']}",
            f"truncated: {result['truncated']}",
            "--- stdout ---",
            result["stdout"],
            "--- stderr ---",
            result["stderr"],
        ]
        return "\n".join(lines)

    # ── File: read ─────────────────────────────────────────────────

    @pack.primitive("read_file")
    def ws_read_file(
        ctx: PrimitiveCallContext,
        path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> str:
        """
        Use: Read file contents with line numbers.
        Input: `path: str` (relative to workspace), `offset: int` (1-indexed start line, 0 = beginning), `limit: int` (max lines, 0 = default 2000).
        Output: Line-numbered text, one line per row.
        Parse: Read lines directly. Line numbers are 1-indexed with " | " separator.
        Parameters:
        - path: File path relative to workspace root.
        - offset: Start from this line (1-indexed). 0 means from beginning.
        - limit: Max lines to read. 0 means default (2000).
        Best Practices:
        - ALWAYS read a file before editing to get exact text for old_string.
        - Use offset/limit for large files to avoid context overflow.
        - Use find_files to discover file structure, then read specific files.
        - Use search to find specific locations before reading.
        - If "Binary file detected", this is not a text file — do not try to read.
        - If "File is empty", the file exists but has no content.
        Output Example:
          1 | import os
          2 | from pathlib import Path
          3 |
          4 | def main():
          5 |     print("hello")
        """
        backend: WorkspaceBackend = ctx.backend
        abs_path, err = backend._resolve_path(path)
        if err:
            return f"Error: {err}"
        if not abs_path.exists():
            return f"Error: File not found: {path}"
        if not abs_path.is_file():
            return f"Error: Not a file: {path}"

        # Binary check
        with open(abs_path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return f"Error: Binary file detected: {path}"

        max_lines = limit if limit > 0 else 2000
        start_line = offset if offset > 0 else 1

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        slice_start = max(0, start_line - 1)
        slice_end = slice_start + max_lines
        selected = lines[slice_start:slice_end]

        result_lines = []
        for i, line in enumerate(selected):
            line_num = slice_start + i + 1
            result_lines.append(f"{line_num} | {line.rstrip()}")

        if not result_lines:
            return f"File is empty: {path}"
        return "\n".join(result_lines)

    # ── File: edit ─────────────────────────────────────────────────

    @pack.primitive("edit_file")
    def ws_edit_file(
        ctx: PrimitiveCallContext,
        path: str,
        old_string: str,
        new_string: str,
    ) -> str:
        """
        Use: Replace an exact string in a file.
        Input: `path: str`, `old_string: str` (must appear exactly once), `new_string: str`.
        Output: "Success." with diff on success, or error message on failure.
        Parse: On success, review the diff to confirm the change is correct.
        Parameters:
        - path: File path relative to workspace root.
        - old_string: Exact text to find and replace. Must appear exactly once in the file.
        - new_string: Replacement text.
        Best Practices:
        - ALWAYS read the file first to copy exact text for old_string.
        - Include 2-3 lines of surrounding context in old_string for uniqueness.
        - old_string is case-sensitive and whitespace-sensitive — copy exactly.
        - For new files, use write_file instead.
        - If "Old string not found": re-read the file, your old_string doesn't match exactly.
        - If "Old string appears N times": include more context to make it unique.
        - If you need multiple replacements, call edit_file multiple times with different strings.
        Output Example (success):
          Success.
          --- diff ---
          --- file.py
          +++ file.py
          @@ -3,4 +3,4 @@
          -    print("old")
          +    print("new")
        """
        import difflib

        backend: WorkspaceBackend = ctx.backend
        abs_path, err = backend._resolve_path(path)
        if err:
            return f"Error: {err}"
        if not abs_path.exists():
            return f"Error: File not found: {path}"

        content = abs_path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return "Error: Old string not found in file."
        if count > 1:
            return f"Error: Old string appears {count} times (must be exactly 1). Include more context."

        # Save for undo
        backend._undo_stack.append({
            "file_path": path,
            "before_content": content,
            "abs_path": str(abs_path),
        })

        new_content = content.replace(old_string, new_string, 1)
        abs_path.write_text(new_content, encoding="utf-8")

        # Generate diff
        diff = "".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=str(abs_path),
                tofile=str(abs_path),
                n=3,
            )
        )
        return f"Success.\n--- diff ---\n{diff}"

    # ── File: write ────────────────────────────────────────────────

    @pack.primitive("write_file")
    def ws_write_file(
        ctx: PrimitiveCallContext,
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> str:
        """
        Use: Create a new file or overwrite an existing one.
        Input: `path: str`, `content: str`, `overwrite: bool` (must be True for existing files).
        Output: "Success." with char count on success, or error message on failure.
        Parse: Read confirmation directly.
        Parameters:
        - path: File path relative to workspace root.
        - content: Complete file content to write.
        - overwrite: Must be True to overwrite existing files. Defaults to False.
        Best Practices:
        - Use for creating NEW files only.
        - For modifying EXISTING files, use edit_file instead — it preserves unchanged content.
        - If "File already exists": use edit_file for edits, or set overwrite=True to replace entirely.
        - Parent directories are created automatically.
        - Do NOT overwrite files you haven't read — you will lose existing content.
        """
        backend: WorkspaceBackend = ctx.backend
        abs_path, err = backend._resolve_path(path)
        if err:
            return f"Error: {err}"
        if abs_path.exists() and not overwrite:
            return f"Error: File already exists. Use overwrite=True or edit_file instead."

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        return f"Success. Wrote {len(content)} chars to {path}"

    # ── File: find (glob) ──────────────────────────────────────────

    @pack.primitive("find_files")
    def ws_find_files(
        ctx: PrimitiveCallContext,
        pattern: str,
        path: str = "",
    ) -> str:
        """
        Use: Find files matching a glob pattern.
        Input: `pattern: str` (glob), `path: str` (optional subdirectory).
        Output: Sorted list of relative paths, one per line, or "No files found."
        Parse: Read the list of file paths. Each line is one file.
        Parameters:
        - pattern: Glob pattern (e.g., '**/*.py', 'src/**/*.ts', '*.md').
        - path: Subdirectory to search in. Empty means workspace root.
        Best Practices:
        - Use to discover file structure before reading specific files.
        - Use ** for recursive matching (e.g., '**/*.py' finds all Python files).
        - Combine with read_file to inspect discovered files.
        - Results are capped at 200 files. Narrow your pattern if you hit the cap.
        - Excludes common non-source directories (.git, node_modules, __pycache__, .venv).
        Output Example:
          src/main.py
          src/utils.py
          tests/test_main.py
        """
        backend: WorkspaceBackend = ctx.backend
        base, err = backend._resolve_base_path(path)
        if err:
            return f"Error: {err}"
        if not base.exists():
            return f"Directory not found: {path}"
        if not base.is_dir():
            return f"Not a directory: {path}"

        EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"}
        MAX_RESULTS = 200

        results = []
        for match in base.glob(pattern):
            if match.is_file():
                display_path = backend._format_path(match)
                if not any(part in EXCLUDE_DIRS for part in Path(display_path).parts):
                    results.append(display_path)
                if len(results) >= MAX_RESULTS:
                    break

        if not results:
            return "No files found."
        results.sort()
        return "\n".join(results)

    # ── File: search (grep) ────────────────────────────────────────

    @pack.primitive("search")
    def ws_search(
        ctx: PrimitiveCallContext,
        pattern: str,
        path: str = "",
        glob: str = "",
        context: int = 2,
    ) -> str:
        """
        Use: Search file contents using regex.
        Input: `pattern: str` (regex), `path: str` (optional subdirectory), `glob: str` (optional file filter), `context: int` (lines around matches, default 2).
        Output: Formatted match results with file headers and context lines.
        Parse: Lines starting with "> " are matches. Lines starting with "  " are context.
        Parameters:
        - pattern: Regex pattern to search for (Python re syntax).
        - path: Subdirectory scope. Empty means workspace root.
        - glob: File pattern filter (e.g., '*.py'). Empty means all files.
        - context: Lines of context before and after each match. Default 2.
        Best Practices:
        - Use to find specific code locations before reading or editing.
        - Use find_files first to narrow file scope, then search with glob filter.
        - Use simple patterns first; escape special regex chars if searching literal text.
        - Results are capped at 100 matches. Narrow pattern or glob if capped.
        - Excludes common non-source directories (.git, node_modules, __pycache__, .venv).
        Output Example:
          --- src/auth.py ---
            10: def authenticate(user, password):
            11:     # Check user credentials.
          > 12:     if not password:
            13:         raise ValueError("Password required")
            14:     return user.hash == hash(password)
        """
        backend: WorkspaceBackend = ctx.backend
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex: {e}"

        base, err = backend._resolve_base_path(path)
        if err:
            return f"Error: {err}"
        if not base.exists():
            return f"Directory not found: {path}"
        if not base.is_dir():
            return f"Not a directory: {path}"

        EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
        MAX_MATCHES = 100

        files: list[Path] = []
        if glob:
            for match in base.rglob(glob):
                if match.is_file():
                    files.append(match)
        else:
            for match in base.rglob("*"):
                if match.is_file():
                    files.append(match)

        output_parts = []
        match_count = 0

        for file_path in files:
            try:
                display_path = backend._format_path(file_path)
                if any(part in EXCLUDE_DIRS for part in Path(display_path).parts):
                    continue
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = text.splitlines()
            matched_line_nums = []
            for i, line in enumerate(lines):
                if regex.search(line):
                    matched_line_nums.append(i)

            if not matched_line_nums:
                continue

            output_parts.append(f"--- {display_path} ---")
            for num in matched_line_nums:
                start = max(0, num - context)
                end = min(len(lines), num + context + 1)
                for j in range(start, end):
                    prefix = ">" if j == num else " "
                    output_parts.append(f"{prefix} {j + 1}: {lines[j]}")

            match_count += len(matched_line_nums)
            if match_count >= MAX_MATCHES:
                output_parts.append(f"... ({match_count} matches, capped at {MAX_MATCHES})")
                break

        if not output_parts:
            return "No matches found."
        return "\n".join(output_parts)

    # ── Web: fetch ─────────────────────────────────────────────────

    @pack.primitive("web_fetch")
    def ws_web_fetch(
        ctx: PrimitiveCallContext,
        url: str,
        timeout: int = 20,
        max_chars: int = 20000,
        file: str = "",
        output_path: str = "",
    ) -> str:
        """
        Use: Fetch an HTTP/HTTPS URL, save full readable text to a workspace file, and return a short preview.
        Input: `url: str`, `timeout: int` (default 20), `max_chars: int` (preview chars, default 20000), `file: str` (optional workspace-relative output file), `output_path: str` (deprecated alias).
        Output: Structured text with success, status_code, final_url, content_type, saved_path, preview_truncated, error, and preview text.
        Parse: Check success first. If true, read saved_path and use read_file on it for the full content; preview is under "--- preview ---".
        Parameters:
        - url: Absolute http:// or https:// URL to fetch.
        - timeout: Max seconds before giving up. Capped to 120 seconds.
        - max_chars: Max preview chars to return. Capped to 20000.
        - file: Workspace-relative file path for the full fetched text. Empty writes under .lambda/webfetch/.
        - output_path: Deprecated alias for file.
        Best Practices:
        - Save the full fetched content to a file and print only a truncated preview plus saved_path.
        - Inspect the full content incrementally with read_file(saved_path, offset=..., limit=...).
        - Use only for public HTTP/HTTPS documentation or pages the user asks you to inspect.
        - Do not fetch local files, internal metadata URLs, or URLs containing secrets.
        - Prefer official docs and cite the final_url in your response when relevant.
        Output Example:
          success: True
          status_code: 200
          final_url: https://example.com/
          saved_path: .lambda/webfetch/example_com.txt
          preview_truncated: False
          --- preview ---
          Example Domain
        """
        import hashlib
        import time as _time

        backend: WorkspaceBackend = ctx.backend
        result = _web_fetch_sync(
            url=url,
            timeout=timeout,
            max_chars=max_chars,
            truncate_text=False,
        )
        preview_limit = max(1, min(max_chars, 20000))
        saved_path = ""
        write_error = ""
        if result["success"]:
            requested_path = file or output_path
            if requested_path:
                abs_path, err = backend._resolve_path(requested_path)
                if err:
                    write_error = err
                    result["success"] = False
                    result["error"] = err
                    abs_path = None
                else:
                    saved_path = requested_path
            else:
                digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
                saved_path = f".lambda/webfetch/{int(_time.time())}_{digest}.txt"
                abs_path, _ = backend._resolve_path(saved_path)
            if result["success"] and abs_path is not None:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(result["text"], encoding="utf-8")

        preview = result["text"]
        preview_truncated = len(preview) > preview_limit
        if preview_truncated:
            preview = preview[:preview_limit] + "\n... [preview truncated; read saved_path for full content]"

        lines = [
            f"success: {result['success']}",
            f"status_code: {result['status_code']}",
            f"final_url: {result['final_url']}",
            f"content_type: {result['content_type']}",
            f"saved_path: {saved_path}",
            f"preview_truncated: {preview_truncated}",
        ]
        if result["error"] or write_error:
            lines.extend(["--- error ---", result["error"] or write_error])
        if preview:
            lines.extend(["--- preview ---", preview])
        return "\n".join(lines)

    # ── Plan: create ───────────────────────────────────────────────

    @pack.primitive("plan_create")
    def ws_plan_create(
        ctx: PrimitiveCallContext,
        title: str,
        goal: str,
        tasks: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Use: Create a new file-backed plan and set it as active.
        Input: `title: str`, `goal: str`, `tasks: list[dict] | None` (optional).
        Output: Confirmation with plan id and file path.
        Parse: Read the plan id for follow-up operations (plan_update_task, plan_get).
        Parameters:
        - title: Short human-readable name for the plan (e.g., "Refactor auth module").
        - goal: Detailed description of what this plan aims to achieve.
        - tasks: Python list of task dicts. Each task: {"title":"...","description":"...","execution_mode":"main_agent","depends_on":[]}.
        Best Practices:
        - Use only for complex multi-step tasks (3+ subtasks), not simple single-step edits.
        - Include 3-10 initial tasks with clear titles and actionable descriptions.
        - Mark independent research/inspection tasks as fork_candidate for parallel execution.
        - Set depends_on to express task ordering (e.g., implement depends on read).
        - After creating a plan, use plan_get(view="ready") to see what to work on next.
        - Example: tasks=[{"title":"Read auth code","description":"Inspect current auth implementation","execution_mode":"main_agent","depends_on":[]}].
        Output Example:
          Created active plan plan_20240101_123456 with 3 task(s).
          Saved to .lambda/plans/plan_20240101_123456.json
        """
        backend: WorkspaceBackend = ctx.backend
        mgr = backend.plan_manager

        plan = mgr.create_plan(title, goal, tasks)
        n = len(plan["tasks"])
        return (
            f"Created active plan {plan['id']} with {n} task(s).\n"
            f"Saved to .lambda/plans/{plan['id']}.json"
        )

    # ── Plan: get ──────────────────────────────────────────────────

    @pack.primitive("plan_get")
    def ws_plan_get(
        ctx: PrimitiveCallContext,
        plan_id: str = "current",
        view: str = "summary",
    ) -> str:
        """
        Use: Read a plan from disk.
        Input: `plan_id: str` (or 'current'), `view: str` ('summary', 'ready', 'full').
        Output: Formatted plan text based on view type.
        Parse: Read the summary, ready tasks, or full plan structure.
        Parameters:
        - plan_id: Plan id or 'current' for the active plan.
        - view: Controls what to show:
          * 'summary' — progress overview (completed/pending/failed counts). Use for quick status checks.
          * 'ready' — list of tasks with status=pending and all depends_on tasks completed. Use to find what to work on next.
          * 'full' — complete plan with all tasks and their states. Use when you need the full picture.
        Best Practices:
        - Use view='ready' at the start of each work cycle to find schedulable tasks.
        - Use view='summary' for quick progress checks between task updates.
        - Only use view='full' when you need the complete plan structure (rare).
        - Plan auto-deactivates when all non-skipped tasks reach a terminal status.
        """
        backend: WorkspaceBackend = ctx.backend
        mgr = backend.plan_manager
        plan = mgr.load_plan(plan_id)
        return mgr.compute_summary(plan, view)

    # ── Plan: update_task ──────────────────────────────────────────

    @pack.primitive("plan_update_task")
    def ws_plan_update_task(
        ctx: PrimitiveCallContext,
        plan_id: str = "current",
        task_id: str = "",
        status: str = "",
        execution_mode: str = "",
        fork_id: str = "",
        result: str = "",
        error: str = "",
    ) -> str:
        """
        Use: Update a single task in the active plan.
        Input: `plan_id: str`, `task_id: str`, and any fields to update (status, execution_mode, fork_id, result, error).
        Output: Confirmation with updated progress counts.
        Parse: Read the confirmation to verify the update was applied.
        Parameters:
        - plan_id: Plan id or 'current' for the active plan.
        - task_id: Task id to update (e.g., 'task_001').
        - status: New task status. Valid: pending, in_progress, completed, failed, blocked, skipped.
        - execution_mode: New execution mode. Valid: main_agent, fork_candidate, forked.
        - fork_id: Fork id for forked tasks (set when you spawn a subagent for this task).
        - result: Task result summary to record (set when completing a task).
        - error: Error message if the task failed.
        Best Practices:
        - Update to status='in_progress' immediately before starting work on a task.
        - For fork tasks: spawn fork → update task with status='in_progress', execution_mode='forked', fork_id=... → do other work → gather → update with status='completed', result=...
        - Write meaningful result summaries so future reads show what was done.
        - When all non-skipped tasks reach terminal status (completed/failed/skipped), the plan auto-deactivates.
        - Only update fields that changed — omit unchanged parameters.
        """
        backend: WorkspaceBackend = ctx.backend
        mgr = backend.plan_manager

        updates = {}
        if status:
            updates["status"] = status
        if execution_mode:
            updates["execution_mode"] = execution_mode
        if fork_id:
            updates["fork_id"] = fork_id
        if result:
            updates["result"] = result
        if error:
            updates["error"] = error

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

    # ── Plan: add_tasks ────────────────────────────────────────────

    @pack.primitive("plan_add_tasks")
    def ws_plan_add_tasks(
        ctx: PrimitiveCallContext,
        plan_id: str = "current",
        tasks: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Use: Add newly discovered tasks to an existing plan.
        Input: `plan_id: str` (or 'current'), `tasks: list[dict] | None`.
        Output: Confirmation with count of added tasks.
        Parse: Read the confirmation to verify tasks were added.
        Parameters:
        - plan_id: Plan id or 'current' for the active plan.
        - tasks: Python list of new task dicts. Each task: {"title":"...","description":"...","depends_on":[]}.
        Best Practices:
        - Use when you discover new subtasks during execution that weren't in the original plan.
        - Set depends_on to express ordering with existing tasks (reference existing task_ids).
        - Keep task titles concise and descriptions actionable.
        - After adding tasks, use plan_get(view="ready") to see updated work queue.
        Output Example:
          Added 2 task(s) to plan_20240101_123456.
          Plan saved to .lambda/plans/plan_20240101_123456.json
        """
        backend: WorkspaceBackend = ctx.backend
        mgr = backend.plan_manager

        if tasks is None:
            tasks = []
        plan = mgr.add_tasks(plan_id, tasks)
        return (
            f"Added {len(tasks)} task(s) to {plan['id']}.\n"
            f"Plan saved to .lambda/plans/{plan['id']}.json"
        )

    return pack
