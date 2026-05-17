"""App launcher - creates and runs the custom TUI."""

from lambda_coding_agent.tui.app import LambdaCodingTUIApp


def launch_tui(
    agent_func,
    workspace: str,
    model_name: str = "unknown",
    git_info: str = "",
    provider_path: str | None = None,
    provider_id: str | None = None,
    environment_block: str = "",
    context_window: int = 200_000,
) -> None:
    """Launch the custom Textual TUI.

    Args:
        agent_func: The llm_chat decorated async generator function.
        workspace: Workspace directory path.
        model_name: Display name of the model.
        git_info: Git branch/status string for the status bar.
        provider_path: Path to provider.json for model switching.
        provider_id: Current provider ID.
        environment_block: Pre-built environment context string.
        context_window: Context window size for the current model.
    """
    app = LambdaCodingTUIApp(
        agent_func=agent_func,
        workspace=workspace,
        model_name=model_name,
        git_info=git_info,
        provider_path=provider_path,
        provider_id=provider_id,
        environment_block=environment_block,
        context_window=context_window,
    )
    app.run()
