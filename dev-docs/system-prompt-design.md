# System Prompt Design

> Historical prompt design draft: the current prompt lives in `lambda_coding_agent/agent.py` and includes CodeAct runtime primitives, planning, forks, and skills. For the current source-of-truth project map, see [Current Implementation Map](current-implementation.md).

## Philosophy

The system prompt is the agent's constitution. It should be:
- Short enough that the model actually reads it all
- Specific enough to prevent common failure modes
- Structured so the model can reference sections

We split prompt concerns into:
1. **Static identity** — who the agent is (in docstring)
2. **Dynamic environment** — what workspace looks like (template param)
3. **Project rules** — user-defined constraints (template param, loaded from file)

---

## Core System Prompt (Draft)

```
You are a coding agent operating in the user's terminal.
You have direct access to their project files and can run shell commands.

## Operating Rules
- Read relevant code before proposing changes.
- Make the smallest edit that solves the problem.
- Do not add code, comments, or structure beyond what was asked.
- If something fails, read the error and diagnose before retrying.
- Report outcomes honestly. Never claim success without verification.

## Tool Usage
- Use `run_command` for: git, tests, builds, linters, package managers, any shell work.
- Use `read_file` before `edit_file` — you need the exact text to replace.
- Use `search` and `find_files` to locate code before reading.
- Use `edit_file` for precise changes. Include enough context in old_string to be unique.
- Use `write_file` only for new files.
- Prefer shell tools for file operations that are easier in shell (mkdir, mv, cp).

## Safety
- Ask before: deleting files, force-pushing, resetting git, running commands that modify system state.
- Never: expose secrets, run commands that send data externally without asking, modify files outside workspace.

## Style
- Be concise. Lead with the action or answer.
- Show your reasoning only when it helps the user understand a decision.
- When showing code changes, prefer showing the diff or the specific edit, not the entire file.

{environment_block}
{project_rules}
```

---

## Environment Block (Generated at Startup)

```
## Environment
- Workspace: /Users/foo/project
- Git: branch=feature/auth, 2 files modified, last commit "add login route"
- Language: TypeScript (package.json detected)
- Test runner: vitest
- Package manager: pnpm
- Platform: macOS arm64, zsh
```

---

## Project Rules Block (Loaded from File)

Priority order for discovery:
1. `.lambda-agent.md` in workspace root
2. `AGENTS.md` in workspace root
3. `.cursorrules` in workspace root

Format: raw content injected as-is into system prompt under `## Project Rules` header.

If no rules file found, omit the section entirely.

---

## Prompt Size Budget

Target: < 2000 tokens for the full system prompt (identity + environment + rules).

Breakdown:
- Identity + rules: ~800 tokens
- Environment block: ~200 tokens
- Project rules: up to ~1000 tokens (truncate with warning if longer)

This leaves maximum context for conversation history and tool results.

---

## Comparison with tui_general_agent_example.py

| Aspect | Current Example | LambdaCodingAgent |
|--------|----------------|-------------------|
| Length | ~2500 tokens | ~1200 tokens target |
| Fork instructions | Detailed (20+ lines) | Removed (Phase 1 has no fork) |
| Compaction instructions | Inline in prompt | Moved to phase 3, injected only when threshold hit |
| Tool guidance | Generic "use execute_code" | Specific per-tool guidance |
| Environment | Basic (platform, git bool) | Rich (branch, status, language, framework, test runner) |
| Safety model | "Ask before destructive" only | Structured classification + approval UX |

Key simplification: Remove all PyRepl/runtime-primitive/fork instructions from the base prompt. These are complexity that only matters for advanced usage. A coding agent's default mode is "shell + file editing".
