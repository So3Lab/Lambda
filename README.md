# LambdaCodingAgent

![LambdaCodingAgent repository cover](https://github.com/So3Lab/Lambda/blob/master/repository-cover.png?raw=true)

LambdaCodingAgent is a local-first coding agent CLI/TUI. It gives an LLM a persistent Python REPL, workspace-scoped runtime primitives, file-backed plans, session history, and a Textual interface for supervising coding work in a repository.

The current implementation is a CodeAct-style agent built on SimpleLLMFunc: the model normally receives one `execute_code` tool, and file, shell, search, web fetch, and plan operations are called inside that REPL as `runtime.workspace.*` primitives.

## Highlights

- **CodeAct runtime**: a persistent PyRepl keeps state across tool calls and exposes workspace primitives.
- **Workspace-safe tools**: shell commands, reads, exact-string edits, writes, glob search, regex search, web fetch, and plan management are scoped to the chosen workspace.
- **Custom Textual TUI**: chat, streamed model output, compact tool cards, fork tabs, status metadata, session controls, rewind, and an always-visible plan panel.
- **File-backed planning**: plans live under `.lambda/plans/` and can be updated by the agent while work progresses.
- **Agent Skills**: user and project skills are discovered automatically and compiled into a compact prompt catalog; the full `SKILL.md` is read only when relevant.
- **Local session state**: TUI sessions are saved in `.lambda/sessions/` for later loading or rewinding.

## Status

This repository is under active development. `dev-docs/current-implementation.md` is the best detailed map of the code that exists today; older files in `dev-docs/` are useful design history and may describe planned architecture rather than current behavior.

## Requirements

- Python `>=3.12,<4.0`
- SimpleLLMFunc
- A provider configuration for an OpenAI-compatible model, unless you only need the no-provider stub mode used by tests/local inspection

Dependency note: `pyproject.toml` currently points `simplellmfunc` at a local development checkout. Make sure SimpleLLMFunc is available at that path, or update the dependency path for your environment before installing.

## Installation

```bash
git clone https://github.com/So3Lab/Lambda.git
cd Lambda
poetry install
```

If you are not using Poetry, create an environment with Python 3.12+ and install the package in editable mode after making the SimpleLLMFunc dependency available.

## Provider configuration

LambdaCodingAgent merges provider config from `~/.lambda/provider.json` and workspace provider config (`<workspace>/.lambda/provider.json` or legacy `<workspace>/provider.json`). Workspace provider entries override duplicate home entries. `--provider-json PATH` bypasses that merge and uses the explicit file.

General config is merged from `~/.lambda/config.json` and `<workspace>/.lambda/config.json`, with workspace values taking precedence. `--model` and `--provider` override merged config values.

Example shape:

```json
{
  "openrouter": [
    {
      "model_name": "openai/gpt-4.1",
      "api_keys": ["sk-..."],
      "base_url": "https://openrouter.ai/api/v1",
      "context_window": 200000
    }
  ]
}
```

`provider.json` is ignored by Git because it can contain API keys.

## Usage

Launch the TUI in the current directory:

```bash
poetry run lambda-agent
```

Choose another workspace:

```bash
poetry run lambda-agent --workspace /path/to/project
```

Run a single non-TUI prompt:

```bash
poetry run lambda-agent --one-shot "Summarize this repository"
```

Common options:

```bash
poetry run lambda-agent --model MODEL_NAME --provider PROVIDER_ID
poetry run lambda-agent --provider-json /path/to/provider.json
```

If the package is installed in your active environment, use `lambda-agent` directly instead of `poetry run lambda-agent`.

## How the agent works

1. `lambda-agent` resolves the workspace, provider config, model override, and environment block.
2. `create_agent()` builds a SimpleLLMFunc `llm_chat` agent.
3. A persistent PyRepl is created for the workspace.
4. Workspace primitives are installed into that REPL as `runtime.workspace.*`.
5. In TUI mode, the custom Textual app consumes the streamed SimpleLLMFunc event stream and renders model output, tool calls, plans, sessions, and forked subagent tabs.

The normal tool model is intentionally small: the model writes Python for `execute_code`, then calls primitives from inside that Python snippet.

### Runtime primitives

| Primitive | Purpose |
| --- | --- |
| `runtime.workspace.run_command()` | Run a shell command in the workspace. |
| `runtime.workspace.read_file()` | Read a workspace-relative text file with line numbers. |
| `runtime.workspace.edit_file()` | Replace an exact string that appears once. |
| `runtime.workspace.write_file()` | Create or overwrite a file. |
| `runtime.workspace.find_files()` | Glob for files. |
| `runtime.workspace.search()` | Regex search files with context. |
| `runtime.workspace.web_fetch()` | Fetch public HTTP/HTTPS content and save the full result under `.lambda/webfetch/` by default. |
| `runtime.workspace.plan_create()` | Create and activate a file-backed plan. |
| `runtime.workspace.plan_get()` | Read the active or named plan. |
| `runtime.workspace.plan_update_task()` | Update task status, result, fork id, or error. |
| `runtime.workspace.plan_add_tasks()` | Add tasks to a plan. |

File primitives are workspace-scoped by default. To allow explicit external roots such as `$HOME/.lambda/` or `$HOME/.agents/`, add `bypass_paths` (or `bypassPaths`) to `~/.lambda/config.json` or `<workspace>/.lambda/config.json`:

```json
{
  "bypass_paths": ["$HOME/.lambda", "$HOME/.agents"]
}
```

## Agent Skills

Skills are regular directories containing a `SKILL.md` file with frontmatter. LambdaCodingAgent discovers skills from:

1. `~/.agents/skills`
2. `~/.lambda/skills`
3. `<workspace>/.agents/skills`
4. `<workspace>/.lambda/skills`

Only a compact catalog is loaded into the prompt. When a user task matches a skill description, the agent should read the full `SKILL.md` through normal file tools before applying the instructions. Project skills shadow user skills with the same name.

## Workspace state and Git hygiene

Generated local state is intentionally ignored:

- `logs/`
- `.lambda/sessions/`
- `.lambda/plans/`
- `.lambda/webfetch/`
- `.lambda/explore_*/`

The `.lambda/` directory itself is **not** blanket-ignored. It remains available for shareable project docs, config, and skills such as `.lambda/skills/<skill-name>/SKILL.md`.

If `git status --ignored` shows `!! .lambda/`, that does not necessarily mean `.lambda` itself is ignored. Git collapses ignored children to the parent directory when every visible file under that directory is ignored. Use this to check the actual rule:

```bash
git check-ignore -v .lambda || true
git check-ignore -v .lambda/sessions/example || true
```

The first command should produce no match; the second should point to the generated-state ignore rule.

## Repository layout

```text
lambda_coding_agent/
├── cli.py                 # CLI entry point and one-shot mode
├── agent.py               # llm_chat agent construction and prompt
├── config.py              # provider.json discovery and overrides
├── skills.py              # Agent Skills discovery and prompt catalog
├── builtin/workspace.py   # runtime.workspace.* primitive pack
├── context/               # environment context construction
├── tools/                 # direct async helper implementations used by primitives/tests
└── tui/                   # custom Textual application, sessions, plan panel, tool cards

tests/                     # focused tests for tools, agent, CLI, TUI, sessions, skills, plans
dev-docs/                  # current implementation map and design history
.lambda/                   # project-local agent metadata; generated subtrees are ignored
```

## Development

Run tests:

```bash
poetry run pytest -q
```

or, from an environment with dependencies installed:

```bash
python -m pytest -q
```

Useful inspection commands:

```bash
python -m lambda_coding_agent.cli --help
git status --short --ignored
```

## License

MIT License. See [LICENSE](LICENSE).
