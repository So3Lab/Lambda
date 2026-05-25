"""Tests for headless mode."""

import io
import json
from dataclasses import dataclass
from datetime import datetime

import pytest

from SimpleLLMFunc.hooks.events import ReactEndEvent, ReActEventType
from SimpleLLMFunc.hooks.stream import EventYield

from lambda_coding_agent.headless import (
    EventWriter,
    HeadlessNDJSONAdapter,
    HeadlessSession,
    json_safe,
    prepare_headless_session,
    run_headless_turn,
)
from lambda_coding_agent.tui.session import SessionManager


class TestEventWriter:
    def test_stdout_writer_emits_ndjson(self):
        stream = io.StringIO()
        writer = EventWriter("-", stream=stream)
        writer.emit({"type": "hello", "value": 1})
        assert json.loads(stream.getvalue()) == {"type": "hello", "value": 1}

    def test_file_writer_emits_ndjson(self, tmp_path):
        path = tmp_path / "events.ndjson"
        writer = EventWriter(str(path))
        writer.emit({"type": "hello"})
        writer.close()
        assert json.loads(path.read_text()) == {"type": "hello"}

    def test_json_safe_handles_common_values(self):
        payload = json_safe({"time": datetime(2024, 1, 2, 3, 4, 5), "error": ValueError("bad")})
        assert payload["time"] == "2024-01-02T03:04:05"
        assert payload["error"] == {"error_type": "ValueError", "message": "bad"}


@pytest.mark.asyncio
async def test_headless_adapter_emits_stable_events():
    stream = io.StringIO()
    writer = EventWriter("-", stream=stream)
    adapter = HeadlessNDJSONAdapter(writer, "sid")

    await adapter.start_model_response("llm_call_1")
    await adapter.append_model_content("llm_call_1", "hello")
    await adapter.start_tool_call("llm_call_1", "tool_1", "execute_code", {"code": "1+1"})
    await adapter.append_tool_argument("tool_1", "code", "print(1)")
    await adapter.set_tool_status("tool_1", "running")
    await adapter.finish_tool_call("tool_1", "ok", "Tool | 0.01s | success", True)
    await adapter.finish_model_response("llm_call_1", "model | 0.02s")

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["type"] for event in events] == [
        "model_start",
        "model_delta",
        "tool_start",
        "tool_argument_delta",
        "tool_status",
        "tool_end",
        "model_end",
    ]
    assert all(event["session_id"] == "sid" for event in events)
    assert events[2]["arguments"] == {"code": "1+1"}


def test_prepare_headless_session_creates_or_loads(tmp_path):
    created = prepare_headless_session(str(tmp_path))
    assert created.session_id
    assert created.history == []

    mgr = SessionManager(str(tmp_path))
    mgr.save_session(created.session_id, history=[{"role": "user", "content": "hi"}], model_name="m", name="n")
    loaded = prepare_headless_session(str(tmp_path), created.session_id)
    assert loaded.session_id == created.session_id
    assert loaded.history == [{"role": "user", "content": "hi"}]
    assert loaded.name == "n"


@dataclass
class _FakeResponse:
    model: str = "fake-model"


async def _fake_agent(message: str, history=None, **kwargs):
    final_messages = list(history or []) + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": "done"},
    ]
    yield EventYield(
        event=ReactEndEvent(
            event_type=ReActEventType.REACT_END,
            timestamp=datetime(2024, 1, 1),
            trace_id="trace",
            func_name="fake_agent",
            iteration=0,
            final_response="done",
            final_messages=final_messages,
            total_iterations=1,
            total_execution_time=0.01,
            total_tool_calls=0,
            total_llm_calls=0,
        )
    )


@pytest.mark.asyncio
async def test_run_headless_turn_saves_session_and_stdout_events(tmp_path):
    output = io.StringIO()
    session = prepare_headless_session(str(tmp_path))
    result = await run_headless_turn(
        agent=_fake_agent,
        prompt="hello",
        workspace=str(tmp_path),
        session=session,
        model_name="model",
        events="-",
        output_stream=output,
        install_signal_handlers=False,
    )

    assert result.session_id == session.session_id
    assert result.history_saved is True
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events[0]["type"] == "session_start"
    assert events[0]["session_id"] == session.session_id
    assert events[1]["type"] == "turn_start"
    assert events[-1]["type"] == "turn_end"
    saved = SessionManager(str(tmp_path)).load_session(session.session_id)
    assert saved["history"][-1] == {"role": "assistant", "content": "done"}


@pytest.mark.asyncio
async def test_run_headless_turn_file_events_announces_session(tmp_path):
    event_path = tmp_path / "events" / "run.ndjson"
    announcement = io.StringIO()
    session = prepare_headless_session(str(tmp_path))

    result = await run_headless_turn(
        agent=_fake_agent,
        prompt="hello",
        workspace=str(tmp_path),
        session=session,
        model_name="model",
        events=str(event_path),
        announcement_stream=announcement,
        install_signal_handlers=False,
    )

    assert result.events_path == str(event_path)
    announced = json.loads(announcement.getvalue())
    assert announced["type"] == "session_start"
    assert announced["events_path"] == str(event_path)
    file_events = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert file_events[0]["type"] == "session_start"
    assert file_events[-1]["type"] == "turn_end"
