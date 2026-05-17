# LambdaCodingAgent - Architecture Plan

> Historical planning note: this file predates the current CodeAct/PyRepl runtime and custom TUI implementation. For the current source-of-truth project map, see [Current Implementation Map](current-implementation.md).

## Vision

A terminal-native coding agent that feels like a fast, knowledgeable pair programmer. It operates inside your project, understands your codebase, runs your tools, and edits your code — all through a Textual TUI or headless stdio mode.

Built on SimpleLLMFunc's `llm_chat` + SelfRef runtime, so context management, forking, and tool orchestration come from the framework. This project focuses on the **coding-agent-specific layer**: the tools, the UX, the project awareness, and the safety model.

---

## Design Principles

1. **Shell-first, Python-second** — Most coding work is shell commands (test, git, build, lint). Shell execution is a first-class tool, not a subprocess hack inside PyRepl.
2. **Precise edits, not overwrites** — File editing uses exact-string-match replacement (like `sed` with literal strings, not regex). The agent sees diffs, not full files.
3. **Project-aware from launch** — Auto-detect language, framework, git state, project rules. Inject relevant context without user config.
4. **Safety by default** — Destructive actions require confirmation. All file edits are reversible. Git state is never force-mutated without explicit ask.
5. **Session-durable** — Conversations persist to disk. Resume where you left off. Context compaction keeps sessions efficient across restarts.
6. **Minimal abstraction** — No plugin systems, no middleware chains, no configuration DSLs. Tools are plain async functions. Behavior is code.

---

## Architecture Layers

```
┌─────────────────────────────────────────────────┐
│                   CLI / TUI                       │
│  argparse → Textual App / stdio mode             │
├─────────────────────────────────────────────────┤
│              Agent Orchestration                  │
│  llm_chat + SelfRef + session persistence        │
├─────────────────────────────────────────────────┤
│                 Tool Layer                        │
│  shell │ edit │ read │ grep │ glob │ web │ git   │
├─────────────────────────────────────────────────┤
│            Project Context Engine                 │
│  auto-detect │ rules loader │ git state          │
├─────────────────────────────────────────────────┤
│            Safety & Approval Gate                 │
│  action classifier │ confirmation UX │ undo log  │
├─────────────────────────────────────────────────┤
│              SimpleLLMFunc Runtime                │
│  llm_chat │ SelfRef │ PyRepl │ Events │ Fork    │
└─────────────────────────────────────────────────┘
```

---

## Module Map (Planned)

```
lambda_coding_agent/
├── cli.py                  # argparse entry, mode dispatch (tui / stdio / one-shot)
├── app.py                  # Textual TUI app (extends SimpleLLMFunc's @tui or custom)
├── agent.py                # Core llm_chat agent definition + system prompt
├── session/
│   ├── persistence.py      # Save/load conversation + selfref state to disk
│   └── resume.py           # Session listing, selection, cleanup
├── tools/
│   ├── shell.py            # run_command: shell execution with streaming, timeout, cwd
│   ├── edit.py             # edit_file: exact-string-replace with diff display
│   ├── read.py             # read_file: line-range reading with line numbers
│   ├── glob.py             # find_files: glob patterns over workspace
│   ├── grep.py             # search: ripgrep-style content search
│   ├── write.py            # write_file: create new files (with overwrite guard)
│   └── web.py              # web_fetch: URL fetch + extract (optional)
├── context/
│   ├── detector.py         # Auto-detect: language, framework, package manager, test runner
│   ├── rules.py            # Load AGENTS.md, .cursorrules, .lambda-agent.md
│   └── git_state.py        # Current branch, status, recent commits for env block
├── safety/
│   ├── classifier.py       # Classify actions: safe / needs-confirmation / blocked
│   ├── approval.py         # TUI confirmation modal or inline prompt
│   └── undo.py             # File edit undo stack (before-snapshots)
└── config.py               # Model config, workspace, preferences
```

---

## Tool Design

### Shell Tool (`run_command`)

The most critical tool. Design:

```python
@tool
async def run_command(
    command: str,
    cwd: str | None = None,
    timeout: int = 120,
    background: bool = False,
) -> dict:
    """
    Run a shell command in the project workspace.

    Args:
        command: The shell command to execute.
        cwd: Working directory (relative to workspace root). Defaults to workspace root.
        timeout: Max seconds before killing. Default 120.
        background: If true, start in background and return immediately.

    Returns:
        stdout, stderr, exit_code, timed_out, duration_ms.

    Best Practices:
        - Use for: git, test runners, linters, build tools, package managers, file operations.
        - Prefer quiet/silent flags to reduce output noise.
        - Do not use for reading file contents (use read_file instead).
        - Ask user before destructive commands (rm -rf, git reset --hard, etc).
    """
```

Key decisions:
- Streams stdout/stderr to TUI in real-time via custom events
- Truncates output at 20k tokens for LLM context, writes full output to temp file
- Background mode for long-running processes (servers, watchers)
- Inherits user's shell environment (PATH, etc.)

### Edit Tool (`edit_file`)

Exact-string-match replacement, not regex:

```python
@tool
async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
) -> dict:
    """
    Replace an exact string in a file.

    Args:
        file_path: Path relative to workspace.
        old_string: The exact text to find (must appear exactly once).
        new_string: The replacement text.

    Returns:
        success, diff preview, line range affected.

    Best Practices:
        - Always read the file first to get the exact text.
        - Include 2-3 lines of surrounding context in old_string to ensure uniqueness.
        - For new files, use write_file instead.
        - For multiple edits to the same file, make them sequentially.
    """
```

Key decisions:
- Fails if `old_string` not found or found multiple times (no silent corruption)
- Returns unified diff for TUI display
- Stores before-snapshot in undo stack
- File hash check to detect external modifications

### Read Tool (`read_file`)

```python
@tool
async def read_file(
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """
    Read file contents with line numbers.

    Args:
        file_path: Path relative to workspace.
        offset: Start from this line (1-indexed). Default: beginning.
        limit: Read at most this many lines. Default: 2000.

    Best Practices:
        - Read before editing so you have exact text for replacements.
        - Use offset/limit for large files instead of reading everything.
        - Prefer grep/glob to find relevant files first.
    """
```

### Glob Tool (`find_files`)

```python
@tool
async def find_files(pattern: str, path: str | None = None) -> list[str]:
    """
    Find files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g., "**/*.py", "src/**/*.ts").
        path: Subdirectory to search in. Default: workspace root.

    Best Practices:
        - Use to discover file structure before reading.
        - Combine with grep for targeted search.
    """
```

### Grep Tool (`search`)

```python
@tool
async def search(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    context: int = 2,
) -> str:
    """
    Search file contents using regex (ripgrep-powered).

    Args:
        pattern: Regex pattern to search for.
        path: Subdirectory scope. Default: workspace root.
        glob: File pattern filter (e.g., "*.py").
        context: Lines of context around matches. Default: 2.

    Best Practices:
        - Use to find code locations before reading/editing.
        - Prefer specific patterns over broad ones.
        - Use glob to narrow file scope.
    """
```

---

## Project Context Engine

On startup, automatically gather and inject:

```
# Environment
- Working directory: /path/to/project
- Git: branch=main, clean=true, recent_commits=[...]
- Language: Python 3.12 (pyproject.toml detected)
- Framework: FastAPI (detected from imports)
- Test runner: pytest
- Package manager: poetry
- Project rules: .lambda-agent.md loaded (14 lines)
```

Detection sources (in priority order):
1. `.lambda-agent.md` / `AGENTS.md` / `.cursorrules` — explicit project rules
2. `pyproject.toml` / `package.json` / `Cargo.toml` / `go.mod` — language + deps
3. Git state — branch, status, recent history
4. Directory structure — src layout, test locations

---

## Safety Model

### Action Classification

| Category | Examples | Behavior |
|----------|----------|----------|
| **Safe** | read_file, grep, glob, find_files | Execute immediately |
| **Monitored** | edit_file, write_file, run_command (non-destructive) | Execute, store undo state |
| **Confirm** | rm, git reset, git push, destructive shell | Show action, wait for user Y/n |
| **Blocked** | Commands matching deny-list patterns | Refuse with explanation |

### Undo Stack

Every `edit_file` and `write_file` stores a before-snapshot:
- In-memory stack for current session
- Can undo last N edits via `/undo` command
- Git stash as escape hatch for "undo everything since session start"

---

## Session Persistence

### Storage Layout

```
~/.lambda-agent/
├── sessions/
│   ├── <session-id>.json        # conversation history + metadata
│   └── <session-id>.selfref.json # selfref state snapshot
├── config.toml                   # global preferences
└── rules/                        # global project rules (optional)
```

### Session Lifecycle

1. **Start**: Create new session or resume last session for this workspace
2. **Each turn**: Auto-save after agent response completes
3. **Compact**: When context is compacted, save compacted state
4. **Resume**: Load history + selfref state, rebuild environment block

### CLI Flags

```
lambda-agent                     # Start in workspace, resume or new session
lambda-agent --new               # Force new session
lambda-agent --resume <id>       # Resume specific session
lambda-agent --workspace /path   # Override workspace
lambda-agent --model <name>      # Override model from provider.json
lambda-agent --one-shot "prompt" # Single turn, no TUI, stdout result
lambda-agent sessions list       # List saved sessions
lambda-agent sessions clean      # Remove old sessions
```

---

## TUI Design

Extend SimpleLLMFunc's Textual TUI with:

1. **Tool confirmation inline** — When a tool needs approval, show the command/action in a highlighted card with [Y] [n] [edit] buttons
2. **Diff display** — File edits show colored unified diff in the tool card
3. **Shell streaming** — Command output streams character-by-character into a scrollable card
4. **Status bar** — Show: model name, token usage, session duration, git branch
5. **Slash commands**:
   - `/undo` — revert last file edit
   - `/compact` — trigger context compaction
   - `/model <name>` — switch model
   - `/cost` — show token/cost summary
   - `/session` — session info

---

## Implementation Phases

### Phase 1: Core Loop (MVP)

Goal: Functional coding agent in TUI with shell + file tools.

- [ ] Shell tool with streaming output
- [ ] Edit tool with exact-string-match
- [ ] Read/grep/glob tools
- [ ] Agent definition with coding-focused system prompt
- [ ] Basic Textual TUI (reuse SimpleLLMFunc's @tui initially)
- [ ] CLI entry with --workspace flag
- [ ] Provider.json config loading

Deliverable: `lambda-agent` runs, can read/edit/search files, run shell commands.

### Phase 2: Project Awareness

- [ ] Auto-detect language/framework/test runner
- [ ] Load project rules files (.lambda-agent.md, AGENTS.md)
- [ ] Git state in environment block
- [ ] Inject relevant project context into system prompt

### Phase 3: Safety & Persistence

- [ ] Action classification (safe/confirm/blocked)
- [ ] TUI confirmation flow for destructive actions
- [ ] File edit undo stack
- [ ] Session save/load to disk
- [ ] --resume / --new CLI flags

### Phase 4: Polish & Advanced

- [ ] Token/cost tracking in status bar
- [ ] Diff display in tool cards
- [ ] Background command support
- [ ] Context compaction integration
- [ ] SelfRef fork for parallel subtasks
- [ ] Web fetch tool (optional)
- [ ] Model switching mid-session

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Shell execution | Dedicated tool, not via PyRepl | Cleaner output, streaming, proper exit codes, visible in TUI |
| File editing | Exact string match, not regex | Safer, deterministic, no regex escaping bugs |
| TUI framework | Textual (via SimpleLLMFunc) | Already a dependency, rich rendering, async-native |
| Session storage | JSON files in ~/.lambda-agent/ | Simple, inspectable, no database dependency |
| Config format | TOML (config) + JSON (provider) | TOML for human editing, JSON for SimpleLLMFunc compat |
| Approval UX | Inline card with Y/n/edit | Non-blocking, shows full context, allows editing before confirm |
| Context management | SelfRef compact + session persistence | Framework handles compaction; we handle disk serialization |
| PyRepl | Keep available but secondary | Useful for complex data processing, but shell is primary tool |

---

## Non-Goals (v1)

- IDE integration / LSP
- Multi-agent orchestration UI (fork is internal, not exposed as UX)
- Plugin/extension system
- Cloud sync for sessions
- Voice input
- Image/screenshot understanding (future, once multimodal tools land)
