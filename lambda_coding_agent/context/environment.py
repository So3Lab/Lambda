"""Environment detection - build context block for system prompt."""

import os
import platform
import subprocess


def build_environment_block(workspace: str) -> str:
    """Build an environment context block for the agent system prompt.

    Detects: workspace path, platform, git state, language/framework.
    """
    lines = ["## Environment"]
    lines.append(f"- Workspace: {workspace}")
    lines.append(f"- Platform: {platform.system()} {platform.machine()}, {os.environ.get('SHELL', 'unknown')}")

    # Git detection
    git_info = _detect_git(workspace)
    if git_info:
        lines.append(f"- Git: {git_info}")

    # Language detection
    lang = _detect_language(workspace)
    if lang:
        lines.append(f"- Language: {lang}")

    # Package manager
    pkg = _detect_package_manager(workspace)
    if pkg:
        lines.append(f"- Package manager: {pkg}")

    return "\n".join(lines)


def _detect_git(workspace: str) -> str | None:
    """Detect git branch and status."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch.returncode != 0:
            return None

        branch_name = branch.stdout.strip()

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
        )
        modified = len([l for l in status.stdout.strip().split("\n") if l.strip()])
        clean = "clean" if modified == 0 else f"{modified} files modified"

        return f"branch={branch_name}, {clean}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _detect_language(workspace: str) -> str | None:
    """Detect primary language from project files."""
    indicators = {
        "pyproject.toml": "Python",
        "setup.py": "Python",
        "requirements.txt": "Python",
        "package.json": "JavaScript/TypeScript",
        "Cargo.toml": "Rust",
        "go.mod": "Go",
        "pom.xml": "Java",
        "build.gradle": "Java/Kotlin",
        "Gemfile": "Ruby",
    }
    for filename, language in indicators.items():
        if os.path.exists(os.path.join(workspace, filename)):
            return language
    return None


def _detect_package_manager(workspace: str) -> str | None:
    """Detect package manager."""
    indicators = {
        "poetry.lock": "poetry",
        "Pipfile.lock": "pipenv",
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
        "package-lock.json": "npm",
        "Cargo.lock": "cargo",
        "go.sum": "go modules",
    }
    for filename, manager in indicators.items():
        if os.path.exists(os.path.join(workspace, filename)):
            return manager
    return None
