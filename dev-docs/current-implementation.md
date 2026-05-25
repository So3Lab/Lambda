# Current Implementation Map

This is the source-of-truth project map for the current codebase. Older docs in
`dev-docs/` are useful design history, but several of them describe planned
architecture rather than the implementation that exists today.

## Quick Summary

LambdaCodingAgent is a Python CLI/TUI coding agent built around a CodeAct-style
loop:

1. `lambda-agent` starts from `lambda_coding_agent.cli:main`.
2. The CLI resolves the workspace, model/provider config, and environment block.
3. `create_agent()` builds a SimpleLLMFunc `llm_chat` agent.
4. The LLM normally gets `execute_code` as its tool.
5. Workspace operations are installed into the persistent PyRepl as
   `runtime.workspace.*` primitives.
6. The custom Textual TUI consumes the streamed SimpleLLMFunc event stream and
   renders chat, tool calls, fork panes, plans, sessions, and status metadata.

## Repository Map

```text
lambda_coding_agent/
├── __init__.py                 # Package metadata.
├── cli.py                      # CLI entry point, config/env setup, TUI/one-shot dispatch.
├── app.py                      # Thin launcher for the custom Textual TUI.
├── agent.py                    # llm_chat agent construction and system prompt.
├── config.py                   # provider.json discovery and model/provider overrides.
├── skills.py                   # Agent Skills discovery and prompt catalog generation.
├── builtin/
│   └── workspace.py            # PyRepl runtime primitive pack: runtime.workspace.*.
├── context/
│   └── environment.py          # Environment block: workspace, platform, git, language, package manager.
├── tools/
│   ├── shell.py                # Direct async shell helper used by tests/legacy tool layer.
│   ├── read.py                 # Direct async file read helper.
│   ├── edit.py                 # Direct async exact-string edit helper + module undo stack.
│   ├── write.py                # Direct async file write helper.
│   ├── glob_tool.py            # Direct async glob helper.
│   ├── grep.py                 # Direct async regex search helper.
│   ├── webfetch.py             # HTTP/HTTPS fetch implementation reused by workspace primitive.
│   └── plan.py                 # File-backed plan manager and direct async plan helpers.
└── tui/
    ├── app.py                  # Custom Textual app and TUIStreamAdapter implementation.
    ├── plan_panel.py           # Always-visible active plan panel.
    ├── session.py              # Workspace-local session JSON persistence.
    ├── tool_cards.py           # Compact tool-call rendering widgets.
    └── screens/
        ├── __init__.py         # Shared modal selection base.
        ├── model_select.py     # Model selection modal.
        ├── session_list.py     # Session list/new/delete modal.
        └── rewind_select.py    # Rewind/fork session modal.

tests/                           # Focused tests for tools, agent, CLI, TUI, sessions, skills, plans.
dev-docs/                        # Design docs and this current implementation map.
provider.json                    # Local OpenAI-compatible provider/model config.
pyproject.toml                   # Package metadata and test config.
```

## Startup and Runtime Flow

### TUI mode

```text
lambda-agent
  -> cli.main()
     -> _resolve_workspace()
     -> load_config()
     -> build_environment_block()
     -> create_agent()
        -> load provider.json through SimpleLLMFunc.OpenAICompatible
        -> discover Agent Skills and build compact skill catalog
        -> create PyRepl(working_directory=workspace)
        -> install build_workspace_pack(workspace, session_id)
        -> decorate agent core with llm_chat(stream=True, self_reference_key="agent_main")
     -> launch_tui()
        -> LambdaCodingTUIApp.run()
```

The TUI builds per-turn template params before invoking the agent:

- `environment_block` from `context/environment.py`.
- `SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM` containing the PyRepl toolset
  with the installed workspace primitive pack.

### One-shot mode

`lambda-agent --one-shot "prompt"` uses the same `create_agent()` path, then
consumes the event stream with a small stdout adapter in `cli._OneShotAdapter`.
It does not launch Textual.

### No-provider mode

If no usable provider is found, `create_agent()` returns a stub callable. The TUI
can still launch for tests or local inspection, but agent responses are stubbed.

## Agent and Prompt Model

`lambda_coding_agent/agent.py` is the main agent definition.

Current behavior:

- The normal tool model is CodeAct: the model writes Python for `execute_code`.
- Workspace operations are called inside the REPL as runtime primitives, for
  example `runtime.workspace.read_file("path.py")`.
- `_make_tools()` currently returns an empty direct-tool list; the effective tool
  surface comes from PyRepl plus runtime primitive installation.
- The system prompt documents operating rules, runtime primitives, planning,
  selfref/fork usage, safety, style, the skill catalog, and the environment
  block.
- `MEMORY_KEY` is `agent_main`.

## Workspace Runtime Primitives

`lambda_coding_agent/builtin/workspace.py` creates the `workspace` primitive pack
installed into the PyRepl.

| Primitive | Current behavior | Notes / limits |
| --- | --- | --- |
| `runtime.workspace.run_command(command, cwd="", timeout=120)` | Runs a shell command in the workspace or workspace-relative cwd with `subprocess.run`, captures stdout/stderr, returns structured text. | Captures after completion; no background mode or TUI streaming inside the primitive. Stdout is capped at 80,000 chars. |
| `runtime.workspace.read_file(path, offset=0, limit=0)` | Reads a workspace-relative text file with line numbers. Default limit is 2,000 lines. | Rejects paths outside workspace and configured bypass roots; rejects null-byte binary files. |
| `runtime.workspace.edit_file(path, old_string, new_string)` | Replaces an exact string that must appear exactly once, writes the file, stores a backend undo snapshot, and returns a unified diff. | Whitespace/case sensitive; no exposed runtime undo primitive. |
| `runtime.workspace.write_file(path, content, overwrite=False)` | Creates parent directories and writes a new file; refuses existing files unless `overwrite=True`. | Prefer `edit_file` for existing files. |
| `runtime.workspace.find_files(pattern, path="")` | Uses `Path.glob`, returns up to 200 sorted paths. | `path` may target the workspace or a configured bypass root; uses a fixed exclude directory list and does not parse `.gitignore`. |
| `runtime.workspace.search(pattern, path="", glob="", context=2)` | Python regex search with context lines and up to 100 matches. | `path` may target the workspace or a configured bypass root; not ripgrep-backed. |
| `runtime.workspace.web_fetch(url, timeout=20, max_chars=20000, file="", output_path="")` | Fetches public HTTP/HTTPS text, saves the full readable text to a workspace file, and returns a short preview plus `saved_path`. | Uses `tools/webfetch.py`; `output_path` is a deprecated alias for `file`. |
| `runtime.workspace.plan_create(...)` | Creates and activates a file-backed plan under `.lambda/plans/`. | Tasks are passed as a Python list of task dicts. |
| `runtime.workspace.plan_get(plan_id="current", view="summary")` | Reads the active or named plan as summary, ready-task view, or full JSON. | Auto-active plan comes from the plan index. |
| `runtime.workspace.plan_update_task(...)` | Updates task status, execution mode, fork id, result, or error. | Auto-deactivates when all non-skipped tasks are terminal. |
| `runtime.workspace.plan_add_tasks(...)` | Adds tasks to an existing plan. | Tasks are passed as a Python list of task dicts. |

The direct async helpers under `lambda_coding_agent/tools/` mirror many of these
operations and are heavily tested, but the active agent prompt directs models to
use the runtime primitives from inside `execute_code`.

Runtime file primitives allow only the workspace by default. Additional external
roots can be configured with `bypass_paths` (or `bypassPaths`) in either
`~/.lambda/config.json` or `<workspace>/.lambda/config.json`, for example
`{"bypass_paths": ["$HOME/.lambda", "$HOME/.agents"]}`.

## TUI Implementation

The current TUI is custom Textual code in `lambda_coding_agent/tui/app.py`; it is
not a SimpleLLMFunc `@tui` wrapper.

Main pieces:

- `LambdaCodingTUIApp` implements the stream adapter methods consumed by
  `SimpleLLMFunc.utils.tui.core.consume_react_stream`.
- Layout contains a `PlanPanel`, main/fork `TabbedContent`, bottom `ChatInput`,
  path autocomplete `OptionList`, and status bar.
- `ChatInput` supports multiline editing: Enter submits, Shift+Enter inserts a
  newline, and up/down move within the input.
- `/` opens Textual's command palette.
- Direct slash handling currently recognizes `/skills refresh` and
  `/refresh skills`.
- Command palette additions are: Switch Model, Sessions, Rewind, Refresh Skills,
  and Clear Chat.
- `@...` file autocomplete searches the configured absolute workspace, not the
  process launch directory. Matching supports nested, partial, and path-segment
  queries and returns up to eight paths.
- Tool calls render through `tui/tool_cards.py` as compact `ToolBlock` widgets
  with status color, summaries, collapsed output, and Ctrl+O expand/collapse.
- Fork subagent streams render in dynamic tabs and are cleaned up after
  completion.
- Status bar shows workspace, git status, model, loaded skill count, and context
  usage percentage when token stats are available.

## Sessions, Plans, and Workspace State

Runtime state is workspace-local unless otherwise noted.

```text
<workspace>/.lambda/
├── sessions/                    # TUI session JSON files.
├── plans/                       # File-backed plan JSON files and plan indexes.
│   └── sessions/                # Session-scoped active-plan indexes.
└── webfetch/                    # Default saved web_fetch outputs.

logs/
└── session_*.log                # CLI stdout/stderr tee logs, relative to launch cwd.
```

Session behavior:

- `SessionManager` saves JSON atomically with `.tmp` then `os.replace`.
- A TUI run starts a fresh session on mount.
- Sessions autosave after turns and on unmount.
- Saved session fields include id, name, timestamps, model/provider metadata,
  context usage, history, and optional active-plan metadata.
- Session selector supports new/load/delete.
- Rewind creates a new forked session by truncating history before a selected
  user message and copying the parent's session-scoped plan index.
- The planned replacement for fork-as-new-session is documented in
  `dev-docs/session-branching-refactor.md`: keep one session per conversation
  workspace and represent rewinds/forks as branches inside a message tree.

Plan behavior:

- `PlanManager` stores plan JSON in `.lambda/plans/`.
- Active plan indexes are session-scoped when a session id exists; otherwise the
  global `.lambda/plans/index.json` is used for compatibility.
- Plan summaries expose overall progress, ready main-agent tasks, ready fork
  candidates, and background fork ids.

## Agent Skills

`lambda_coding_agent/skills.py` implements Agent Skills as automatic context
compilation, not as runtime primitives.

Discovery roots, in order:

1. `~/.agents/skills`
2. `~/.lambda/skills`
3. `<workspace>/.agents/skills`
4. `<workspace>/.lambda/skills`

Each skill is a directory containing `SKILL.md` with frontmatter. The prompt gets
only a compact catalog with name, description, location, and scope. When a task
matches a skill description, the model is instructed to read the full `SKILL.md`
through normal file tools before using it. Project skills shadow user skills of
the same name.

## Config and Environment Detection

`config.py` merges configuration at startup:

- `~/.lambda/config.json` plus `<workspace>/.lambda/config.json`, with workspace values overriding duplicate home values.
- Provider config from `~/.lambda/provider.json`, `<workspace>/.lambda/provider.json`, and legacy `<workspace>/provider.json`, with workspace provider entries overriding duplicate home entries.
- Explicit `--provider-json` bypasses provider merging; `--model` and `--provider` override merged config values.

`context/environment.py` currently detects:

- workspace path;
- platform and shell;
- git branch and clean/modified count;
- language from common root files such as `pyproject.toml`, `package.json`,
  `go.mod`, etc.;
- package manager from lockfiles such as `poetry.lock`, `package-lock.json`,
  `Cargo.lock`, etc.

It does not currently load project rule files, detect test runners, detect
frameworks, or include recent commit summaries.

## Test Map

Important test coverage:

- `tests/test_shell.py`, `test_read.py`, `test_edit.py`, `test_write.py`,
  `test_glob.py`, `test_grep.py`, `test_webfetch.py` cover direct tool helpers
  and the web fetch primitive wrapper.
- `tests/test_plan.py` covers `PlanManager` and direct plan helpers.
- `tests/test_agent.py`, `test_config.py`, `test_cli.py`, `test_environment.py`,
  `test_skills.py` cover startup/config/prompt/environment/skills behavior.
- `tests/test_tui.py`, `test_tui_integration.py`, `test_command_palette.py`,
  `test_session.py`, `test_incremental_render.py`, and `test_new_features.py`
  cover TUI rendering, command palette, sessions, input behavior, path
  autocomplete, tool cards, model switching hooks, and related UX behavior.

## Existing Docs Status

The older `dev-docs/` files should be read as design history unless updated:

- `architecture.md` is the original architecture plan. Its planned module map
  includes packages that do not exist (`session/`, `safety/`) and omits current
  modules (`builtin/workspace.py`, `skills.py`, `tui/`, plan and web fetch
  tools). Its tool descriptions also include planned streaming/background and
  safety behavior that is not fully implemented.
- `phase1-implementation.md` is a Phase 1 build guide. It describes direct
  `@tool` wiring and SimpleLLMFunc `@tui` reuse, while the current project uses
  PyRepl runtime primitives and a custom Textual TUI.
- `system-prompt-design.md` is a draft prompt design. The current prompt is in
  `agent.py` and explicitly documents `execute_code`, runtime primitives,
  selfref/fork usage, planning, and skills.
- `decisions.md` captures historical rationale. Some decisions changed in the
  current implementation: shell/file operations are exposed as workspace
  primitives inside PyRepl, and the UI is custom Textual rather than `@tui`.

## Known Implementation Gaps vs Historical Plans

- No dedicated `safety/` package or confirmation modal is implemented yet.
- Shell execution in the runtime primitive is captured, not streamed, and has no
  background process mode.
- Search is Python regex scanning, not ripgrep-backed.
- File globbing uses fixed exclude directories, not `.gitignore`/`pathspec`.
- Environment detection is intentionally small and does not load project rules.
- Session storage is workspace-local `.lambda/sessions/`, not
  `~/.lambda-agent/sessions/` with separate selfref snapshots.
- Direct async tool helpers and runtime workspace primitives coexist; the current
  agent-facing surface is the runtime primitive pack.

## Maintenance Notes

- Treat this file as the current implementation map.
- When changing the agent-facing tool surface, update both `agent.py` prompt
  examples and the workspace primitive table above.
- When changing TUI input/autocomplete behavior, preserve configured-workspace
  scoping for `@` path autocomplete and recursive/partial/path-segment matching.
- When changing session or plan persistence, update the state layout and session
  sections above.
