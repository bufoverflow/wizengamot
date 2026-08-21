# Wizengamot repository instructions

## Scope

Wizengamot is a domain-neutral orchestration framework for Claude Code subagents and independent Claude Agent SDK sessions. Framework code, tests, schemas, documentation, and fictional examples are public. Project knowledge, private agent prompts, customer material, model outputs, and local configuration remain outside the public boundary.

## Engineering rules

- Preserve dry-run defaults and explicit aggregate budget authorization.
- Treat model sessions as untrusted workers. Validate identity, schema, result status, and filesystem paths deterministically.
- Keep batch workers read-only unless a reviewed workflow explicitly expands their permissions.
- Separate source analysis from synthesis.
- Keep workspace-specific assumptions out of `src/wizengamot/`.
- Keep Python compatibility at 3.11 or newer.
- Run `make verify` after changes.

## Privacy rules

- Never move private workspace content into tracked framework files.
- Never commit `knowledge/`, `workspace/`, local Claude state, MCP configuration, transcripts, credentials, or generated reports.
- Run `python scripts/privacy_check.py --staged` before commits.
- Build releases through `python scripts/build_public_release.py`.
