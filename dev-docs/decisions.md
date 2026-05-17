# Key Design Decisions

> Historical decisions note: some decisions evolved with the current CodeAct/PyRepl runtime and custom TUI. For the current source-of-truth project map, see [Current Implementation Map](current-implementation.md).

## Why Not Just Extend tui_general_agent_example.py?

The example is a demo of SimpleLLMFunc capabilities (PyRepl, SelfRef, Fork). A good coding agent has different priorities:

1. **Shell is primary** — The example routes everything through `execute_code` (Python). Real coding is 60% shell commands. A dedicated shell tool gives better UX, streaming, and tool cards.

2. **File editing must be precise** — The example uses `sed` (regex on line ranges) and `echo_into` (full overwrite). Both are error-prone for code. Exact-string-match is the proven approach (used by Cursor, Aider, Claude CLI).

3. **Simpler prompt** — The example has a 2500-token prompt covering fork semantics, compaction protocol, runtime primitives. A coding agent prompt should be < 1200 tokens focused purely on coding behavior.

4. **No PyRepl as primary tool** — PyRepl is powerful but adds complexity. For coding agents, shell + file tools cover 95% of needs. PyRepl can be an optional add-on for data work.

---

## Why Exact-String-Match Editing?

Alternatives considered:

| Approach | Pros | Cons |
|----------|------|------|
| Regex sed | Powerful patterns | Easy to corrupt code, escaping hell |
| Full file rewrite | Simple | Expensive (tokens), easy to lose content |
| Line-number based | Precise targeting | Fragile after multi-edit, model miscounts lines |
| Diff/patch apply | Standard format | Models generate broken patches frequently |
| **Exact string match** | **Deterministic, safe, verifiable** | **Requires reading file first** |

Exact string match wins because:
- Zero ambiguity — either the string is found exactly once, or the edit fails safely
- Forces the model to read before editing (good practice anyway)
- Easy to show diffs to user
- Easy to undo (store the old string)

---

## Why Dedicated Shell Tool vs PyRepl subprocess?

| Aspect | PyRepl subprocess.run | Dedicated Shell Tool |
|--------|----------------------|---------------------|
| Token cost | Extra Python wrapper code | Direct command string |
| Streaming | Must capture manually | Native event streaming |
| TUI rendering | Shows as "execute_code" | Shows as "run_command" with proper card |
| Exit code | Must parse from dict | First-class return field |
| Working directory | Must os.chdir or pass cwd | Native cwd parameter |
| Shell env | Inherits Python env only | Inherits full user shell (PATH, aliases) |

---

## Why No Plugin System?

The temptation: make tools pluggable so users can add their own.

The reality:
- Users who can write plugins can just fork and add tools directly
- Plugin discovery, loading, schema validation adds 500+ lines of infra code
- Every plugin system eventually becomes its own maintenance burden
- The tool list is small and stable; adding a tool is one file + one import

Decision: Tools are just Python files in `tools/`. Want a new tool? Add a file. No registry, no discovery, no config.

---

## Session Persistence: JSON vs SQLite

| Aspect | JSON files | SQLite |
|--------|-----------|--------|
| Inspectable | Yes (open in editor) | No (need sqlite3 CLI) |
| Dependencies | None | sqlite3 (stdlib, but schema migration) |
| Concurrent access | Fine (one writer) | Better (transactions) |
| Complexity | ~50 lines | ~200 lines + migrations |
| Search across sessions | Manual | SQL queries |

Decision: JSON files for v1. A coding agent session is single-writer, rarely searched across sessions, and inspectability helps debugging.

---

## TUI: Reuse @tui vs Build Custom?

SimpleLLMFunc's `@tui` provides:
- Textual app with input box, scrollable output
- Tool card rendering for known tools
- Stream consumption and event routing
- Fork column display
- Virtualization for long sessions

What we need on top:
- Custom tool cards for our tools (shell streaming, diff display)
- Confirmation modal for dangerous actions
- Status bar (model, tokens, git branch)
- Slash commands (/undo, /compact, /model)

Plan: Start with `@tui` and custom event hooks. If we hit limits, subclass `AgentTUIApp`. Only build a fully custom app if the `@tui` approach proves too constrained.

---

## Model Configuration: Inherit SimpleLLMFunc's provider.json

No need to invent a new config format. SimpleLLMFunc already has `provider.json` loading:

```python
models = OpenAICompatible.load_from_json_file("provider.json")
llm = models["provider_id"]["model_name"]
```

Our config layer just needs:
1. Find provider.json (workspace → ~/.lambda-agent/ → error)
2. Select model (--model flag → config default → first model)
3. Pass to agent

We add a thin `~/.lambda-agent/config.toml` for preferences only:
```toml
[model]
provider = "openrouter"
name = "gpt-5.4"

[behavior]
auto_compact_threshold = 0.3
confirm_destructive = true
```
