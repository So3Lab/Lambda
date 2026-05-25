"""Tests for workspace runtime primitive path bypass configuration."""

import json

from SimpleLLMFunc.runtime.primitives import PrimitiveCallContext

from lambda_coding_agent.builtin.workspace import build_workspace_pack


def _entry(pack, name):
    return next(e for e in pack.primitives if e.name == f"workspace.{name}")


def _ctx(pack, name):
    return PrimitiveCallContext(
        primitive_name=f"workspace.{name}",
        call_id="test",
        execution_id="test",
        backend=pack.backend,
    )


def test_read_file_allows_configured_bypass_path(tmp_path):
    workspace = tmp_path / "workspace"
    bypass = tmp_path / "outside" / ".agents"
    workspace.mkdir()
    bypass.mkdir(parents=True)
    (bypass / "SKILL.md").write_text("skill docs\n", encoding="utf-8")
    (workspace / ".lambda").mkdir()
    (workspace / ".lambda" / "config.json").write_text(
        json.dumps({"bypass_paths": [str(bypass)]}),
        encoding="utf-8",
    )

    pack = build_workspace_pack(str(workspace))
    result = _entry(pack, "read_file").handler(
        _ctx(pack, "read_file"),
        str(bypass / "SKILL.md"),
    )

    assert "1 | skill docs" in result


def test_read_file_rejects_unconfigured_outside_path(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")

    pack = build_workspace_pack(str(workspace))
    result = _entry(pack, "read_file").handler(
        _ctx(pack, "read_file"),
        str(outside / "secret.txt"),
    )

    assert "outside workspace" in result.lower()


def test_write_edit_find_and_search_allow_configured_bypass_path(tmp_path):
    workspace = tmp_path / "workspace"
    bypass = tmp_path / "outside" / ".lambda"
    workspace.mkdir()
    bypass.mkdir(parents=True)
    (workspace / ".lambda").mkdir()
    (workspace / ".lambda" / "config.json").write_text(
        json.dumps({"bypassPaths": [str(bypass)]}),
        encoding="utf-8",
    )

    pack = build_workspace_pack(str(workspace))
    bypass_file = bypass / "note.txt"
    write_result = _entry(pack, "write_file").handler(
        _ctx(pack, "write_file"),
        str(bypass_file),
        "old target\n",
    )
    edit_result = _entry(pack, "edit_file").handler(
        _ctx(pack, "edit_file"),
        str(bypass_file),
        "old",
        "new",
    )
    find_result = _entry(pack, "find_files").handler(
        _ctx(pack, "find_files"),
        "*.txt",
        path=str(bypass),
    )
    search_result = _entry(pack, "search").handler(
        _ctx(pack, "search"),
        "target",
        path=str(bypass),
    )

    assert "Success" in write_result
    assert "Success" in edit_result
    assert str(bypass_file) in find_result
    assert f"--- {bypass_file} ---" in search_result

def test_ascii_tilde_bypass_path_is_treated_as_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    bypass = home / ".agents"
    workspace.mkdir()
    bypass.mkdir(parents=True)
    (bypass / "SKILL.md").write_text("skill docs\n", encoding="utf-8")
    (workspace / ".lambda").mkdir()
    (workspace / ".lambda" / "config.json").write_text(
        json.dumps({"bypass_paths": ["~/.agents"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    pack = build_workspace_pack(str(workspace))
    result = _entry(pack, "read_file").handler(
        _ctx(pack, "read_file"),
        str(bypass / "SKILL.md"),
    )

    assert "1 | skill docs" in result

