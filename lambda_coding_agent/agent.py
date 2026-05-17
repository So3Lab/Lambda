"""Agent definition — CodeAct agent with workspace primitives via PyRepl."""

from typing import Any

from SimpleLLMFunc import OpenAICompatible, llm_chat, tool
from SimpleLLMFunc.builtin import PyRepl
from SimpleLLMFunc.runtime.selfref import (
    SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM,
)

from lambda_coding_agent.builtin.workspace import build_workspace_pack
from lambda_coding_agent.skills import build_skill_catalog_block, discover_skills

MEMORY_KEY = "agent_main"


def _make_tools(workspace: str, session_id: str | None = None):
    """Create tool functions bound to a workspace.

    In the CodeAct model, all workspace operations are PyRepl primitives.
    The only tool call is execute_code; everything else runs inside it.
    """
    return []


def _build_system_prompt(skill_catalog_block: str = "") -> str:
    """Build the system prompt for the coding agent."""
    skills_section = f"\n{skill_catalog_block}\n" if skill_catalog_block else ""
    return f"""You are a coding agent operating in the user's terminal.
You have direct access to their project files and can run shell commands.

## Operating Rules
- Read relevant code before proposing changes.
- Make the smallest edit that solves the problem.
- Do not add code, comments, or structure beyond what was asked.
- If something fails, read the error and diagnose before retrying.
- Report outcomes honestly. Never claim success without verification.

## Code Execution Model
You operate through a Python REPL (execute_code). This is your only tool call.
All workspace operations are available as runtime primitives, called inside execute_code:

  runtime.workspace.run_command("git status")          — shell commands
  runtime.workspace.read_file("file.py")               — read files (returns line-numbered text)
  runtime.workspace.edit_file("file.py", old, new)     — exact string replacement
  runtime.workspace.write_file("file.py", content)     — create/overwrite files
  runtime.workspace.find_files("**/*.py")              — glob patterns
  runtime.workspace.search("pattern", glob="*.py")     — regex search
  runtime.workspace.web_fetch("https://example.com")    — fetch public HTTP/HTTPS pages
  runtime.workspace.plan_create("Title", "Goal", tasks='[{{"title":"T1","description":"...","execution_mode":"main_agent","depends_on":[]}}]')
  runtime.workspace.plan_get(view="summary")           — "summary" | "ready" | "full"
  runtime.workspace.plan_update_task(task_id="task_001", status="completed")
  runtime.workspace.plan_add_tasks(tasks='[{{"title":"T2","depends_on":["task_001"]}}]')

Use runtime.list_primitives() to see all available primitives.
Use runtime.get_primitive_spec("workspace.xxx") for full documentation of any primitive.

### Typical Workflow
1. **Observe**: Read relevant files, run commands to understand the codebase.
   ```python
   runtime.workspace.find_files("**/*.py")
   runtime.workspace.read_file("src/main.py")
   runtime.workspace.run_command("git status")
   ```
2. **Plan** (for complex tasks with 3+ substeps): Create a plan to track progress.
   ```python
   runtime.workspace.plan_create("Refactor auth", "Migrate to JWT", tasks='[
     {{"title":"Read current auth code","description":"Inspect auth module","execution_mode":"main_agent","depends_on":[]}},
     {{"title":"Implement JWT","description":"Add jwt encoding/decoding","execution_mode":"main_agent","depends_on":["task_001"]}},
     {{"title":"Update tests","description":"Fix test suite","execution_mode":"main_agent","depends_on":["task_002"]}}
   ]')
   ```
3. **Execute**: Work through tasks, updating plan as you go.
   ```python
   runtime.workspace.plan_update_task(task_id="task_001", status="in_progress")
   runtime.workspace.read_file("src/auth.py")
   runtime.workspace.edit_file("src/auth.py", old_string="...", new_string="...")
   runtime.workspace.run_command("pytest tests/test_auth.py")
   runtime.workspace.plan_update_task(task_id="task_001", status="completed", result="Migrated to jwt.decode")
   ```
4. **Verify**: Run tests, check output, confirm the change works.
   ```python
   runtime.workspace.run_command("pytest -q")
   ```

### When to Use Plans
- **Use plans** when: task has 3+ substeps, involves multiple files, needs progress tracking, or has fork subagents.
- **Skip plans** when: simple single-file edit, quick bug fix, one command to run.

### Error Handling
- If a primitive returns an error, read the error message, diagnose the cause, then retry with a fix.
- If edit_file fails with "not found" or "appears N times", re-read the file to get exact text.
- If run_command fails, check exit_code and stderr before retrying.

## Python REPL (execute_code / reset_repl)
- Variables persist across execute_code calls.
- Runtime primitives are available as runtime.namespace.name(...).
- Do not reassign the runtime variable. REPL state is persistent.
- reset_repl clears REPL variables but not selfref context.

## Runtime Primitives (selfref)
- runtime.selfref.context.inspect() — read-only snapshot of current context.
- runtime.selfref.context.remember(text) — store a durable experience.
- runtime.selfref.context.forget(experience_id) — remove an experience.
- runtime.selfref.context.compact(...) — checkpoint context for compaction.
- runtime.selfref.fork.spawn(message) — spawn a child agent asynchronously.
- runtime.selfref.fork.gather_all(...) — collect results from spawned children.
- Use fork/sub-agent parallelism for independent, parallelizable subtasks.
- When delegating to a fork, include goal, scope, inputs, and required outputs.
- Your selfref key is "{MEMORY_KEY}"; do not read or write any other key.

## Fork Subagent Rules
- When spawning a fork, instruct it to reply with plain text summarizing findings.
- Forks must NOT use print() or code execution to communicate results — reply directly in text.
- For long findings, forks should write results to a file and reply with the file path.
- Fork messages should be self-contained: include goal, scope, inputs, and expected output format.

## Plan Tool Workflow
For complex multi-step tasks, use the file-backed plan primitives.
Plans live in .lambda/plans/ as JSON files — treat the plan file as the source of truth.
Keep chat responses concise; do not paste the full plan unless the user asks.

When using fork subagents:
- Identify independent research/inspection tasks as fork candidates.
- Spawn ready fork candidates early; immediately record fork_id via runtime.workspace.plan_update_task.
- Do NOT gather immediately if ready main-agent tasks exist that don't depend on fork results.
- Continue local work while forks run in the background.
- Gather at synchronization points; write fork result summaries into plan tasks.

## Safety
- Ask before: deleting files, force-pushing, resetting git, running destructive commands.
- Never: expose secrets, modify files outside workspace.

## Style
- Be concise. Lead with the action or answer.
- Show reasoning only when it helps understanding.
{skills_section}
{{environment_block}}
"""


def create_agent(
    provider_path: str | None,
    workspace: str,
    environment_block: str,
    model_name: str | None = None,
    provider_id: str | None = None,
    session_id: str | None = None,
):
    """Create the coding agent function.

    Args:
        provider_path: Path to provider.json (None for testing/mock).
        workspace: Workspace root path.
        environment_block: Pre-built environment context string.
        model_name: Model name override.
        provider_id: Provider ID override.

    Returns:
        The decorated llm_chat function, or a stub if no provider available.
    """
    # Load LLM interface
    llm = None
    if provider_path:
        try:
            models = OpenAICompatible.load_from_json_file(provider_path)
            if provider_id and model_name:
                llm = models[provider_id][model_name]
            elif provider_id:
                provider_models = models[provider_id]
                llm = next(iter(provider_models.values()))
            else:
                first_provider = next(iter(models.values()))
                llm = next(iter(first_provider.values()))
        except Exception:
            llm = None

    tools = _make_tools(workspace)
    skill_catalog = discover_skills(workspace)
    system_prompt = _build_system_prompt(build_skill_catalog_block(skill_catalog.skills))

    # Create persistent Python REPL with runtime primitives (selfref)
    repl = PyRepl(working_directory=workspace)

    # Install workspace primitives pack (CodeAct primitives)
    workspace_pack = build_workspace_pack(workspace, session_id)
    repl.install_pack(workspace_pack)

    def _build_runtime_toolkit():
        return [*repl.toolset]

    if llm is None:
        # Return a stub callable for testing when no LLM is configured
        async def _stub_agent(message: str, history=None, **kwargs):
            """Stub agent for testing (no LLM configured)."""
            return "[Stub agent: no LLM configured]", history or []

        _stub_agent._tools = tools
        _stub_agent._system_prompt = system_prompt
        _stub_agent._repl = repl
        return _stub_agent

    # Define agent function with dynamic docstring via closure
    async def _agent_core(message: str, history=None):
        pass

    _agent_core.__doc__ = system_prompt
    _agent_core.__name__ = "coding_agent"
    _agent_core.__qualname__ = "coding_agent"

    decorated = llm_chat(
        llm_interface=llm,
        toolkit=tools,
        stream=True,
        self_reference_key=MEMORY_KEY,
    )(_agent_core)

    # Attach helpers for the TUI to build template params each turn
    decorated._repl = repl
    decorated._build_runtime_toolkit = _build_runtime_toolkit
    decorated._environment_block = environment_block

    return decorated
