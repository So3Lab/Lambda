# Phase 1 Implementation Guide

## Goal

A working coding agent CLI that can:
- Run shell commands with streaming output
- Read, search, and edit files precisely
- Maintain conversation in a Textual TUI
- Connect to any OpenAI-compatible model via provider.json

---

## Step-by-step Build Order

### 1. Tool Layer (build and test independently)

Build tools as plain `@tool`-decorated async functions. Test each tool in isolation before wiring to the agent.

#### 1.1 Shell Tool

```
lambda_coding_agent/tools/shell.py
```

Requirements:
- `asyncio.create_subprocess_shell` with PIPE for stdout/stderr
- Stream output line-by-line via `event_emitter` (CustomEvent with `event_name="shell_stdout"` / `"shell_stderr"`)
- Capture full output, truncate for return value if > 20k tokens
- Kill on timeout, return `timed_out: true`
- Support `cwd` relative to workspace root
- Inherit user's PATH and shell environment

Test: run `echo hello`, `ls`, `sleep 5` with timeout=2, verify streaming + truncation.

#### 1.2 File Read Tool

```
lambda_coding_agent/tools/read.py
```

Requirements:
- Read file with line numbers (`{lineno} | {content}`)
- Support offset + limit for large files
- Paths relative to workspace root, reject path traversal (`../` above workspace)
- Binary file detection (return error message, not garbage)

#### 1.3 File Edit Tool

```
lambda_coding_agent/tools/edit.py
```

Requirements:
- Find `old_string` in file content exactly once
- Replace with `new_string`
- Return unified diff (3 lines context)
- Store before-content in undo stack (module-level list for now)
- Fail clearly if: not found, found multiple times, file doesn't exist

#### 1.4 File Write Tool

```
lambda_coding_agent/tools/write.py
```

Requirements:
- Create new file or overwrite existing
- For existing files: require explicit `overwrite=True` parameter
- Store before-content in undo stack if overwriting
- Create parent directories as needed

#### 1.5 Glob Tool

```
lambda_coding_agent/tools/glob_tool.py
```

Requirements:
- Use `pathlib.Path.glob` or `glob.glob` with recursive support
- Respect .gitignore patterns (use `pathspec` library or simple exclusion list)
- Return sorted list of relative paths
- Cap results at 200 entries

#### 1.6 Grep Tool

```
lambda_coding_agent/tools/grep.py
```

Requirements:
- Use subprocess to call `rg` (ripgrep) if available, fallback to Python regex scan
- Support: pattern, path scope, file glob filter, context lines
- Return formatted matches with file:line:content
- Cap output at 100 matches

---

### 2. Agent Definition

```
lambda_coding_agent/agent.py
```

Define the core `@llm_chat` function:
- System prompt focused on coding agent behavior (draw from tui_general_agent_example.py but simplified)
- Toolkit = [run_command, read_file, edit_file, write_file, find_files, search]
- Stream mode enabled
- SelfRef key for context management
- Template params for environment block injection

Key system prompt sections:
- Core rules (read before edit, prefer small changes, diagnose before retry)
- Tool usage guidance (when to use which tool)
- Output discipline (concise, actionable)
- Safety (ask before destructive actions)

---

### 3. CLI Entry Point

```
lambda_coding_agent/cli.py
```

Minimal argparse:
- `--workspace` (default: cwd)
- `--model` (override model name from provider.json)
- `--provider` (path to provider.json, default: ./provider.json or ~/.lambda-agent/provider.json)

Startup sequence:
1. Parse args
2. Resolve workspace
3. Load model from provider.json
4. Build environment block (git state, detected language)
5. Launch TUI

---

### 4. TUI Integration

```
lambda_coding_agent/app.py
```

For Phase 1, directly use SimpleLLMFunc's `@tui` decorator:
- Wire the agent function with `@tui`
- Add custom event hooks for shell streaming display
- Add tool card rendering for our custom tools

If the built-in TUI is too rigid, subclass `AgentTUIApp` and override rendering.

---

## File Checklist (Phase 1)

```
lambda_coding_agent/
├── __init__.py
├── cli.py              # Entry point
├── app.py              # TUI setup
├── agent.py            # Core agent definition
├── config.py           # Model + workspace config loading
├── tools/
│   ├── __init__.py     # Export all tools
│   ├── shell.py        # run_command
│   ├── read.py         # read_file
│   ├── edit.py         # edit_file
│   ├── write.py        # write_file
│   ├── glob_tool.py    # find_files
│   └── grep.py         # search
└── context/
    ├── __init__.py
    └── environment.py  # Build env block (git, platform, workspace)
```

---

## Testing Strategy

Each tool gets a focused test file:

```
tests/
├── test_shell.py       # Run commands, test timeout, streaming
├── test_edit.py        # String replacement, failure modes, undo
├── test_read.py        # Line ranges, path traversal rejection
├── test_grep.py        # Pattern matching, result caps
└── test_glob.py        # Pattern matching, gitignore respect
```

Test tools directly (call `tool.func(...)`) without LLM involvement.

Integration test: one test that runs the full agent with a mock LLM to verify tool wiring.

---

## Dependencies to Add

```toml
# Already have via SimpleLLMFunc:
# textual, rich, pydantic, openai, httpx

# May need to add:
pathspec = "^0.12"  # For .gitignore pattern matching in glob
```

---

## Success Criteria (Phase 1 Done)

- [ ] `lambda-agent` launches TUI in current directory
- [ ] Agent can run `ls`, `git status`, `pytest` via run_command with streaming output
- [ ] Agent can read files with line numbers
- [ ] Agent can edit files with exact-string-match (shows diff in TUI)
- [ ] Agent can search codebase with grep + glob
- [ ] Agent creates new files when needed
- [ ] Conversation works multi-turn with history
- [ ] Works with any OpenAI-compatible model via provider.json
