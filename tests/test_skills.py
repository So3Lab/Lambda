"""Tests for Agent Skills support."""

from __future__ import annotations

from pathlib import Path

from lambda_coding_agent.agent import create_agent
from lambda_coding_agent.skills import (
    build_skill_catalog_block,
    discover_skills,
    parse_skill_file,
)


def _write_skill(
    root: Path,
    dirname: str,
    *,
    name: str | None = None,
    description: str | None = "Useful skill description.",
    body: str = "# Instructions\n\nDo the specialized workflow.",
    extra_frontmatter: str = "",
) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = ["---"]
    if name is not None:
        frontmatter.append(f"name: {name}")
    else:
        frontmatter.append(f"name: {dirname}")
    if description is not None:
        frontmatter.append(f"description: {description}")
    if extra_frontmatter:
        frontmatter.append(extra_frontmatter.rstrip())
    frontmatter.append("---")
    (skill_dir / "SKILL.md").write_text("\n".join(frontmatter) + "\n\n" + body, encoding="utf-8")
    return skill_dir


class TestSkillParsing:
    def test_parse_skill_file_extracts_frontmatter_and_body(self, tmp_path):
        skill_dir = _write_skill(
            tmp_path,
            "pdf-processing",
            description="Extract PDF text and forms. Use when the user mentions PDFs.",
            body="# PDF workflow\n\n1. Inspect the document.",
            extra_frontmatter='license: Apache-2.0\nmetadata:\n  author: test-org\nallowed-tools: Bash(pdftotext:*) Read',
        )

        skill, diagnostics = parse_skill_file(skill_dir / "SKILL.md", scope="project")

        assert diagnostics == []
        assert skill is not None
        assert skill.name == "pdf-processing"
        assert skill.description == "Extract PDF text and forms. Use when the user mentions PDFs."
        assert skill.body == "# PDF workflow\n\n1. Inspect the document."
        assert skill.license == "Apache-2.0"
        assert skill.metadata == {"author": "test-org"}
        assert skill.allowed_tools == "Bash(pdftotext:*) Read"
        assert skill.skill_dir == skill_dir.resolve()

    def test_parse_is_lenient_for_colons_and_name_mismatch(self, tmp_path):
        skill_dir = _write_skill(
            tmp_path,
            "actual-dir",
            name="declared-name",
            description="Use when: the user asks for reports: summaries, charts, or tables.",
        )

        skill, diagnostics = parse_skill_file(skill_dir / "SKILL.md", scope="project")

        assert skill is not None
        assert skill.name == "declared-name"
        assert skill.description == "Use when: the user asks for reports: summaries, charts, or tables."
        assert any("does not match" in d for d in diagnostics)
        assert any("does not match" in w for w in skill.warnings)

    def test_parse_skips_skill_without_description(self, tmp_path):
        skill_dir = _write_skill(tmp_path, "missing-description", description=None)

        skill, diagnostics = parse_skill_file(skill_dir / "SKILL.md", scope="project")

        assert skill is None
        assert any("description" in d.lower() for d in diagnostics)


class TestSkillDiscovery:
    def test_discovers_project_and_user_skills_with_project_precedence(self, tmp_path):
        workspace = tmp_path / "workspace"
        home = tmp_path / "home"
        _write_skill(
            home / ".agents" / "skills",
            "shared",
            description="User-level shared skill.",
        )
        _write_skill(
            workspace / ".agents" / "skills",
            "shared",
            description="Project-level shared skill.",
        )
        _write_skill(
            workspace / ".lambda" / "skills",
            "project-only",
            description="Project native skill.",
        )

        catalog = discover_skills(str(workspace), home=str(home))

        by_name = {skill.name: skill for skill in catalog.skills}
        assert set(by_name) == {"shared", "project-only"}
        assert by_name["shared"].description == "Project-level shared skill."
        assert by_name["shared"].scope == "project"
        assert by_name["project-only"].scope == "project"
        assert any("shadow" in d.lower() for d in catalog.diagnostics)

    def test_discovery_skips_symlinked_project_skill_outside_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside"
        real_skill = _write_skill(outside, "external", description="External skill should not load.")
        skills_root = workspace / ".agents" / "skills"
        skills_root.mkdir(parents=True)
        (skills_root / "external").symlink_to(real_skill, target_is_directory=True)

        catalog = discover_skills(str(workspace), home=str(tmp_path / "home"))

        assert catalog.skills == []
        assert any("outside" in d.lower() for d in catalog.diagnostics)


class TestSkillCatalogPrompt:
    def test_catalog_block_discloses_only_metadata_and_usage_instruction(self, tmp_path):
        skill_dir = _write_skill(
            tmp_path / ".agents" / "skills",
            "pdf-processing",
            description="Extract PDF text. Use when working with PDFs.",
            body="SECRET BODY SHOULD NOT BE IN CATALOG",
        )
        skill, _ = parse_skill_file(skill_dir / "SKILL.md", scope="project")

        block = build_skill_catalog_block([skill])

        assert "<available_skills>" in block
        assert "pdf-processing" in block
        assert "Extract PDF text" in block
        assert "When a task matches a skill" in block
        assert "Read the SKILL.md" in block
        assert "SECRET BODY" not in block

    def test_create_agent_injects_skill_catalog_when_available(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        _write_skill(
            tmp_path / ".agents" / "skills",
            "code-review",
            description="Review code changes. Use when asked for code review.",
            body="Detailed review checklist should load only on activation.",
        )

        agent = create_agent(
            provider_path=None,
            workspace=str(tmp_path),
            environment_block="## Environment\n- test",
        )

        assert "<available_skills>" in agent._system_prompt
        assert "code-review" in agent._system_prompt
        assert "Review code changes" in agent._system_prompt
        assert "When a task matches a skill" in agent._system_prompt
        assert "Read the SKILL.md" in agent._system_prompt
        assert "Detailed review checklist" not in agent._system_prompt

    def test_create_agent_omits_catalog_when_no_skills_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        agent = create_agent(
            provider_path=None,
            workspace=str(tmp_path),
            environment_block="## Environment\n- test",
        )

        assert "<available_skills>" not in agent._system_prompt
