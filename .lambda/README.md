# .lambda

This directory is reserved for LambdaCodingAgent workspace metadata that may be useful to share with the project, such as project Agent Skills under `.lambda/skills/<skill-name>/SKILL.md`.

Generated local runtime state is ignored by Git:

- `sessions/` — TUI session JSON files
- `plans/` — file-backed agent plans and active-plan indexes
- `webfetch/` — saved `runtime.workspace.web_fetch()` outputs
- `explore_*/` — generated exploration snapshots

The `.lambda/` directory itself is intentionally not blanket-ignored. Keeping this README tracked prevents Git from collapsing the directory to `!! .lambda/` when showing ignored generated children.

Do not store secrets here. Provider credentials belong in `provider.json`, which is ignored by Git.
