"""Agent Skills discovery and prompt catalog support."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """Parsed Agent Skill metadata."""

    name: str
    description: str
    location: Path
    skill_dir: Path
    scope: str
    body: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class SkillCatalog:
    """Discovered skill records plus diagnostics."""

    skills: list[Skill]
    diagnostics: list[str] = field(default_factory=list)


_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_skill_file(path: str | Path, scope: str) -> tuple[Skill | None, list[str]]:
    """Parse a SKILL.md file.

    Parsing is intentionally lenient for cross-client compatibility: simple
    unquoted scalar values may contain colons, and cosmetic name issues only
    produce diagnostics. A missing/empty description skips the skill.
    """
    skill_path = Path(path).resolve()
    diagnostics: list[str] = []

    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"Cannot read {skill_path}: {exc}"]

    frontmatter, body, error = _split_frontmatter(text)
    if error:
        return None, [f"{skill_path}: {error}"]

    data, parse_diagnostics = _parse_frontmatter(frontmatter)
    diagnostics.extend(f"{skill_path}: {d}" for d in parse_diagnostics)

    name = str(data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()
    if not description:
        return None, diagnostics + [f"{skill_path}: description is required"]
    if not name:
        name = skill_path.parent.name
        diagnostics.append(f"{skill_path}: name missing; using directory name {name}")

    warnings: list[str] = []
    dirname = skill_path.parent.name
    if name != dirname:
        warnings.append(f"name '{name}' does not match parent directory '{dirname}'")
    if len(name) > 64:
        warnings.append(f"name '{name}' exceeds 64 characters")
    if not _NAME_RE.match(name):
        warnings.append(f"name '{name}' does not match recommended lowercase-hyphen format")
    diagnostics.extend(f"{skill_path}: {warning}" for warning in warnings)

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = {str(k): str(v) for k, v in metadata.items()}

    skill = Skill(
        name=name,
        description=description,
        location=skill_path,
        skill_dir=skill_path.parent,
        scope=scope,
        body=body.strip(),
        license=str(data.get("license", "")).strip(),
        compatibility=str(data.get("compatibility", "")).strip(),
        metadata=metadata,
        allowed_tools=str(data.get("allowed-tools", "")).strip(),
        warnings=warnings,
    )
    return skill, diagnostics


def discover_skills(workspace: str, home: str | None = None) -> SkillCatalog:
    """Discover skills from project and user skill directories."""
    workspace_path = Path(workspace).expanduser().resolve()
    home_path = Path(home).expanduser().resolve() if home is not None else Path.home().resolve()

    roots: list[tuple[Path, str, Path]] = [
        (home_path / ".agents" / "skills", "user", home_path),
        (home_path / ".lambda" / "skills", "user", home_path),
        (workspace_path / ".agents" / "skills", "project", workspace_path),
        (workspace_path / ".lambda" / "skills", "project", workspace_path),
    ]

    by_name: dict[str, Skill] = {}
    order: list[str] = []
    diagnostics: list[str] = []

    for root, scope, allowed_root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            try:
                resolved_dir = skill_dir.resolve()
                resolved_dir.relative_to(allowed_root)
            except ValueError:
                diagnostics.append(f"Skipping skill outside {scope} root: {skill_dir}")
                continue

            skill_file = resolved_dir / "SKILL.md"
            if not skill_file.is_file():
                continue

            skill, parse_diagnostics = parse_skill_file(skill_file, scope=scope)
            diagnostics.extend(parse_diagnostics)
            if skill is None:
                continue

            existing = by_name.get(skill.name)
            if existing is None:
                by_name[skill.name] = skill
                order.append(skill.name)
                continue

            if existing.scope == "user" and skill.scope == "project":
                by_name[skill.name] = skill
                diagnostics.append(
                    f"Project skill '{skill.name}' shadows user skill at {existing.location}"
                )
            else:
                diagnostics.append(
                    f"Skill '{skill.name}' at {skill.location} shadowed by {existing.location}"
                )

    return SkillCatalog(skills=[by_name[name] for name in order], diagnostics=diagnostics)


def build_skill_catalog_block(skills: list[Skill]) -> str:
    """Build the compact prompt block for available skills."""
    if not skills:
        return ""

    lines = [
        "## Available Skills",
        "The following skills provide specialized instructions for specific tasks.",
        "When a task matches a skill's description, Read the SKILL.md at the listed location before proceeding.",
        "Only the compact skill catalog is loaded here; do not assume the full skill instructions are already in context.",
        "",
        "<available_skills>",
    ]
    for skill in sorted(skills, key=lambda s: s.name):
        lines.extend(
            [
                "  <skill>",
                f"    <name>{escape(skill.name)}</name>",
                f"    <description>{escape(skill.description)}</description>",
                f"    <location>{escape(str(skill.location))}</location>",
                f"    <scope>{escape(skill.scope)}</scope>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def _split_frontmatter(text: str) -> tuple[str, str, str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", "", "missing opening frontmatter delimiter"
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]), ""
    return "", "", "missing closing frontmatter delimiter"


def _parse_frontmatter(frontmatter: str) -> tuple[dict[str, Any], list[str]]:
    data: dict[str, Any] = {}
    diagnostics: list[str] = []
    current_map: str | None = None

    for raw_line in frontmatter.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        if raw_line.startswith((" ", "\t")):
            if current_map is None:
                diagnostics.append(f"ignored indented line without parent key: {raw_line.strip()}")
                continue
            key, sep, value = raw_line.strip().partition(":")
            if not sep:
                diagnostics.append(f"ignored malformed metadata line: {raw_line.strip()}")
                continue
            mapping = data.setdefault(current_map, {})
            if isinstance(mapping, dict):
                mapping[key.strip()] = _clean_scalar(value.strip())
            continue

        current_map = None
        key, sep, value = raw_line.partition(":")
        if not sep:
            diagnostics.append(f"ignored malformed frontmatter line: {raw_line.strip()}")
            continue
        key = key.strip()
        value = value.strip()
        if value == "" and key == "metadata":
            data[key] = {}
            current_map = key
        else:
            data[key] = _clean_scalar(value)

    return data, diagnostics


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
