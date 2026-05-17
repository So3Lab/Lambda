"""CLI entry point for lambda-agent."""

import argparse
import io
import os
import subprocess
import sys
from datetime import datetime

from lambda_coding_agent.agent import create_agent
from lambda_coding_agent.app import launch_tui
from lambda_coding_agent.config import load_config
from lambda_coding_agent.context.environment import build_environment_block

LOG_DIR = "logs"


class TeeStream:
    """Tee stdout/stderr to both the original stream and a log file."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        self._original.write(data)
        self._log_file.write(data)
        self._log_file.flush()

    def flush(self):
        self._original.flush()
        self._log_file.flush()

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        return self._original.isatty()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _setup_logging():
    """Set up tee'd stdout/stderr to a timestamped log file.

    Returns the log file path.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"session_{timestamp}.log")

    log_file = open(log_path, "w", encoding="utf-8")

    sys.stdout = TeeStream(sys.stdout, log_file)
    sys.stderr = TeeStream(sys.stderr, log_file)

    # Store log file handle so we can close it on exit
    sys._lambda_log_file = log_file
    sys._lambda_log_path = log_path

    return log_path


def _teardown_logging():
    """Restore original streams and close log file. Returns log path."""
    log_path = getattr(sys, "_lambda_log_path", None)
    log_file = getattr(sys, "_lambda_log_file", None)

    if isinstance(sys.stdout, TeeStream):
        sys.stdout = sys.stdout._original
    if isinstance(sys.stderr, TeeStream):
        sys.stderr = sys.stderr._original

    if log_file:
        log_file.close()

    return log_path


def _resolve_workspace(workspace: str | None) -> str:
    """Resolve workspace to absolute path."""
    if workspace is None:
        return os.getcwd()
    return os.path.abspath(os.path.expanduser(workspace))


def _detect_git_info(workspace: str) -> str:
    """Quick git info for the status bar."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if branch.returncode != 0:
            return ""
        branch_name = branch.stdout.strip()

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=3,
        )
        modified = len([l for l in status.stdout.strip().split("\n") if l.strip()])
        clean = "clean" if modified == 0 else f"{modified} modified"
        return f"{branch_name}, {clean}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


class _OneShotAdapter:
    """Minimal TUIStreamAdapter that prints to stdout for one-shot mode."""

    def __init__(self):
        self.content_parts: list[str] = []
        self._current_tool: str | None = None

    async def start_model_response(self, model_call_id: str) -> None:
        pass

    async def append_model_content(self, model_call_id: str, content_delta: str) -> None:
        self.content_parts.append(content_delta)
        print(content_delta, end="", flush=True)

    async def append_model_reasoning(self, model_call_id: str, reasoning_delta: str) -> None:
        pass

    async def finish_model_response(self, model_call_id: str, stats_line: str) -> None:
        print()

    async def start_tool_call(self, model_call_id: str, tool_call_id: str, tool_name: str, arguments: dict) -> None:
        self._current_tool = tool_name
        args_str = " ".join(f"{k}={v!r}" for k, v in arguments.items())
        print(f"\n[tool] {tool_name}({args_str})", flush=True)

    async def append_tool_output(self, tool_call_id: str, output_delta: str) -> None:
        pass

    async def clear_tool_input(self, tool_call_id: str) -> None:
        pass

    async def finish_tool_call(self, tool_call_id: str, result_markdown: str, stats_line: str, success: bool) -> None:
        status = "ok" if success else "FAILED"
        print(f"[tool] {status} {stats_line}", flush=True)
        self._current_tool = None


def _run_one_shot(agent, prompt: str) -> None:
    """Run a single agent turn and print to stdout."""
    import asyncio

    from SimpleLLMFunc.hooks.abort import AbortSignal
    from SimpleLLMFunc.utils.tui.core import consume_react_stream

    async def _run():
        abort_signal = AbortSignal()
        adapter = _OneShotAdapter()

        # Build template params for runtime toolkit override
        template_params: dict = {}
        if hasattr(agent, "_environment_block"):
            template_params["environment_block"] = agent._environment_block
        if hasattr(agent, "_build_runtime_toolkit"):
            from SimpleLLMFunc.runtime.selfref import (
                SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM,
            )
            template_params[SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM] = (
                agent._build_runtime_toolkit()
            )

        stream = agent(
            message=prompt,
            history=[],
            _abort_signal=abort_signal,
            _template_params=template_params if template_params else None,
        )
        await consume_react_stream(stream=stream, adapter=adapter, abort_signal=abort_signal)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[interrupted]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LambdaCodingAgent - A practical coding agent CLI"
    )
    parser.add_argument(
        "--workspace",
        help="Workspace directory (default: current directory)",
        default=None,
    )
    parser.add_argument(
        "--model",
        help="Model name override",
        default=None,
    )
    parser.add_argument(
        "--provider",
        help="Provider ID override",
        default=None,
    )
    parser.add_argument(
        "--provider-json",
        help="Path to provider.json",
        default=None,
    )
    parser.add_argument(
        "--one-shot",
        help="Single prompt mode (no TUI)",
        default=None,
        metavar="PROMPT",
    )
    args = parser.parse_args()

    workspace = _resolve_workspace(args.workspace)
    if not os.path.isdir(workspace):
        print(f"Error: Workspace is not a directory: {workspace}", file=sys.stderr)
        sys.exit(1)

    config = load_config(
        workspace=workspace,
        model=args.model,
        provider_id=args.provider,
        provider_path=args.provider_json,
    )

    # Build environment context
    env_block = build_environment_block(workspace)

    # Create agent
    agent = create_agent(
        provider_path=config.provider_path,
        workspace=config.workspace,
        environment_block=env_block,
        model_name=config.model_name,
        provider_id=config.provider_id,
    )

    if args.one_shot:
        _run_one_shot(agent, args.one_shot)
        return

    # Detect git info for status bar
    git_info = _detect_git_info(workspace)

    # Determine model name for display
    display_model = config.model_name or "unknown"
    context_window = 200_000
    if config.provider_path:
        try:
            import json
            with open(config.provider_path) as f:
                data = json.load(f)
            first_provider = next(iter(data.values()))
            if display_model == "unknown":
                first_model = first_provider[0] if isinstance(first_provider, list) else next(iter(first_provider.values()))
                display_model = first_model.get("model_name", "unknown")
                context_window = first_model.get("context_window", 200_000)
            else:
                # Find the matching model entry for context_window
                models_list = first_provider if isinstance(first_provider, list) else list(first_provider.values())
                for m in models_list:
                    if m.get("model_name") == display_model:
                        context_window = m.get("context_window", 200_000)
                        break
        except Exception:
            pass

    # Tee all stdout/stderr to a log file before TUI takes over the terminal
    log_path = _setup_logging()

    # Check if LLM is available
    if getattr(agent, "_system_prompt", None) is not None:
        print("=" * 50)
        print("LambdaCodingAgent")
        print("=" * 50)
        print(f"Workspace: {workspace}")
        print()
        print("WARNING: No LLM configured.")
        print("Create a provider.json in your workspace or ~/.lambda-agent/")
        print()
        print("Example provider.json:")
        print('  {"openrouter": [{"model_name": "gpt-4", "api_keys": ["sk-..."], "base_url": "https://openrouter.ai/api/v1"}]}')
        print()
        print("TUI will launch but agent responses will be stubbed.")
        print("=" * 50)
        print()

    try:
        launch_tui(
            agent_func=agent,
            workspace=workspace,
            model_name=display_model,
            git_info=git_info,
            provider_path=config.provider_path,
            provider_id=config.provider_id,
            environment_block=env_block,
            context_window=context_window,
        )
    finally:
        _teardown_logging()
        print(f"\nSession log saved to: {log_path}")


if __name__ == "__main__":
    main()
