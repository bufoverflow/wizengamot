# Workspace format

A workspace is identified by `wizengamot.workspace.json` and `registry/agents.json`.

## Required directories

```text
agent-library/
campaigns/
registry/
schemas/
tasks/
```

Interactive use additionally requires `.claude/agents/`.

## Agent definitions

Claude Code agent definitions are Markdown files with YAML frontmatter. The registry entry and frontmatter `name` must match exactly. Names use lowercase letters, numbers, and hyphens.

## Context paths

Each agent may define a `context` array. Paths must be relative, remain within the workspace, and exist during validation. The engine also supplies workspace-wide instruction and required-context files from `wizengamot.workspace.json`.

## Campaigns

A campaign contains selectors, a campaign prompt, default concurrency, and a default per-agent ceiling. Selectors can target tiers, domains, roles, and exact names.

## Validation

Validation checks:

- expected tier counts
- unique and valid agent names
- safe relative paths
- agent file existence
- frontmatter identity
- context file existence
- default interactive count
- output schema shape
- campaign parsing and non-empty selections
- audit campaign worker count
