"""Headless runner and NDJSON event adapter."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass, is_dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, TextIO

from SimpleLLMFunc.hooks.abort import AbortSignal
from SimpleLLMFunc.runtime.selfref import SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM
from SimpleLLMFunc.utils.tui.core import consume_react_stream

from lambda_coding_agent.tui.session import SessionManager


@dataclass
class HeadlessSession:
    """Loaded or newly-created headless session state."""

    session_id: str
    history: list[dict[str, Any]]
    name: str = ""
    created_at: str | None = None
    last_ctx_usage: int = 0


@dataclass
class HeadlessResult:
    """Result of one headless turn."""

    session_id: str
    history_saved: bool
    aborted: bool
    events_path: str | None = None


def json_safe(value: Any) -> Any:
    """Convert common framework values into JSON-serializable data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__, "message": str(value)}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if is_dataclass(value):
        return json_safe(asdict(value))
    return str(value)


class EventWriter:
    """Write one JSON object per line to stdout or a file."""

    def __init__(self, target: str = "-", stream: TextIO | None = None) -> None:
        self.target = target or "-"
        self.path: str | None = None if self.target == "-" else self.target
        self._owns_stream = False
        if self.path is None:
            self._stream = stream or sys.stdout
        else:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._stream = open(self.path, "a", encoding="utf-8")
            self._owns_stream = True

    def emit(self, event: dict[str, Any]) -> None:
        payload = json_safe(event)
        self._stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        self._stream.write("\n")
        self._stream.flush()

    def close(self) -> None:
        if self._owns_stream:
            self._stream.close()


class HeadlessNDJSONAdapter:
    """TUIStreamAdapter implementation that emits stable NDJSON events."""

    def __init__(self, writer: EventWriter, session_id: str) -> None:
        self.writer = writer
        self.session_id = session_id

    def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("session_id", self.session_id)
        self.writer.emit(event)

    async def start_model_response(self, model_call_id: str) -> None:
        self._emit({"type": "model_start", "model_call_id": model_call_id})

    async def append_model_content(self, model_call_id: str, content_delta: str) -> None:
        self._emit({
            "type": "model_delta",
            "model_call_id": model_call_id,
            "delta": content_delta,
        })

    async def append_model_reasoning(self, model_call_id: str, reasoning_delta: str) -> None:
        self._emit({
            "type": "model_reasoning_delta",
            "model_call_id": model_call_id,
            "delta": reasoning_delta,
        })

    async def finish_model_response(self, model_call_id: str, stats_line: str) -> None:
        self._emit({
            "type": "model_end",
            "model_call_id": model_call_id,
            "stats": stats_line,
        })

    async def start_tool_call(
        self,
        model_call_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
    ) -> None:
        self._emit({
            "type": "tool_start",
            "model_call_id": model_call_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments,
        })

    async def append_tool_output(self, tool_call_id: str, output_delta: str) -> None:
        self._emit({
            "type": "tool_output_delta",
            "tool_call_id": tool_call_id,
            "delta": output_delta,
        })

    async def append_tool_argument(
        self,
        tool_call_id: str,
        argname: str,
        argcontent_delta: str,
    ) -> None:
        self._emit({
            "type": "tool_argument_delta",
            "tool_call_id": tool_call_id,
            "argname": argname,
            "delta": argcontent_delta,
        })

    async def set_tool_status(self, tool_call_id: str, status: str) -> None:
        self._emit({
            "type": "tool_status",
            "tool_call_id": tool_call_id,
            "status": status,
        })

    async def request_tool_input(
        self,
        tool_call_id: str,
        request_id: str,
        prompt: str,
    ) -> None:
        self._emit({
            "type": "tool_input_request",
            "tool_call_id": tool_call_id,
            "request_id": request_id,
            "prompt": prompt,
        })

    async def clear_tool_input(self, tool_call_id: str) -> None:
        self._emit({"type": "tool_input_clear", "tool_call_id": tool_call_id})

    async def finish_tool_call(
        self,
        tool_call_id: str,
        result_markdown: str,
        stats_line: str,
        success: bool,
    ) -> None:
        self._emit({
            "type": "tool_end",
            "tool_call_id": tool_call_id,
            "success": success,
            "stats": stats_line,
            "result": result_markdown,
        })


def prepare_headless_session(workspace: str, session_id: str | None = None) -> HeadlessSession:
    """Create a session id or load an existing session."""
    manager = SessionManager(workspace)
    if session_id:
        data = manager.load_session(session_id)
        return HeadlessSession(
            session_id=session_id,
            history=data.get("history", []),
            name=data.get("name", ""),
            created_at=data.get("created_at"),
            last_ctx_usage=int(data.get("last_ctx_usage", 0) or 0),
        )
    return HeadlessSession(session_id=manager.start_new_session(), history=[])


def _build_template_params(agent: Any) -> dict[str, Any] | None:
    template_params: dict[str, Any] = {}
    if hasattr(agent, "_environment_block"):
        template_params["environment_block"] = agent._environment_block
    if hasattr(agent, "_build_runtime_toolkit"):
        template_params[SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM] = (
            agent._build_runtime_toolkit()
        )
    return template_params or None


def _install_sigint_handler(abort_signal: AbortSignal, on_abort: Any) -> Any:
    loop = asyncio.get_running_loop()

    def request_abort() -> None:
        if not abort_signal.is_aborted:
            abort_signal.abort("user interrupted")
            on_abort("user interrupted")

    try:
        loop.add_signal_handler(signal.SIGINT, request_abort)
        return ("loop", loop)
    except (NotImplementedError, RuntimeError):
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda _sig, _frame: request_abort())
        return ("signal", previous)


def _restore_sigint_handler(token: Any) -> None:
    if token is None:
        return
    kind, value = token
    if kind == "loop":
        value.remove_signal_handler(signal.SIGINT)
    elif kind == "signal":
        signal.signal(signal.SIGINT, value)


async def run_headless_turn(
    *,
    agent: Any,
    prompt: str,
    workspace: str,
    session: HeadlessSession,
    model_name: str,
    provider_id: str | None = None,
    events: str = "-",
    output_stream: TextIO | None = None,
    announcement_stream: TextIO | None = None,
    install_signal_handlers: bool = True,
) -> HeadlessResult:
    """Run one headless agent turn and emit NDJSON events."""
    writer = EventWriter(events, stream=output_stream)
    manager = SessionManager(workspace)
    session_event = {
        "type": "session_start",
        "session_id": session.session_id,
        "workspace": os.path.abspath(workspace),
    }
    if writer.path:
        session_event["events_path"] = writer.path
        announcement = announcement_stream or sys.stdout
        announcement.write(json.dumps(json_safe(session_event), ensure_ascii=False, separators=(",", ":")))
        announcement.write("\n")
        announcement.flush()
    writer.emit(session_event)

    abort_signal = AbortSignal()
    abort_emitted = False

    def emit_abort(reason: str) -> None:
        nonlocal abort_emitted
        if abort_emitted:
            return
        abort_emitted = True
        writer.emit({
            "type": "abort",
            "session_id": session.session_id,
            "reason": reason,
        })

    sigint_token = None
    if install_signal_handlers:
        sigint_token = _install_sigint_handler(abort_signal, emit_abort)

    new_history: list[dict[str, Any]] = []
    error: BaseException | None = None
    history_saved = False
    try:
        writer.emit({
            "type": "turn_start",
            "session_id": session.session_id,
            "message": prompt,
        })
        stream = agent(
            message=prompt,
            history=session.history,
            _abort_signal=abort_signal,
            _template_params=_build_template_params(agent),
        )
        consumed_history = await consume_react_stream(
            stream=stream,
            adapter=HeadlessNDJSONAdapter(writer, session.session_id),
            abort_signal=abort_signal,
        )
        if consumed_history:
            new_history = list(consumed_history)
    except BaseException as exc:
        error = exc
        writer.emit({
            "type": "error",
            "session_id": session.session_id,
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
    finally:
        _restore_sigint_handler(sigint_token)
        history_to_save = new_history if new_history else session.history
        name = session.name or prompt[:60].replace("\n", " ")
        manager.save_session(
            session.session_id,
            history=history_to_save,
            model_name=model_name,
            name=name,
            provider_id=provider_id or "",
            last_ctx_usage=session.last_ctx_usage,
            created_at=session.created_at,
        )
        history_saved = True
        writer.emit({
            "type": "turn_end",
            "session_id": session.session_id,
            "history_saved": history_saved,
            "aborted": abort_signal.is_aborted,
            "error": error is not None,
        })
        writer.close()

    if error is not None:
        raise error

    return HeadlessResult(
        session_id=session.session_id,
        history_saved=history_saved,
        aborted=abort_signal.is_aborted,
        events_path=writer.path,
    )


def run_headless_turn_sync(**kwargs: Any) -> HeadlessResult:
    """Synchronous wrapper for CLI entry points."""
    return asyncio.run(run_headless_turn(**kwargs))
