# Architecture

## Design goal

Wizengamot separates a reusable orchestration engine from project-specific intelligence. The engine controls selection, planning, permissions, budgets, execution, persistence, validation, and release hygiene. A workspace supplies the domain model, agents, campaigns, context, and output contract.

## Execution planes

### Interactive plane

Claude Code discovers project-scoped agents under a workspace's `.claude/agents/` directory. The default roster should remain small: one chief and a limited number of directors. Specialists, councils, and synthesizers are activated selectively.

### Batch source plane

The Agent SDK runner creates one fresh `query()` session per selected agent. Workers share reviewed workspace files and task instructions while carrying no shared conversational state. The runner denies mutation and nested delegation.

### Synthesis plane

Synthesis agents consume completed source reports after the independent pass. They preserve material dissent, reconcile evidence, and produce decision artifacts with source-report paths.

## Trust boundary

Model output remains untrusted until deterministic checks confirm:

- expected agent identity
- valid result status
- required structured fields
- safe paths
- stable run identity
- explicit operator authorization

The framework performs no automatic canonical writeback.

## Agent topology

A large workspace can combine:

- chief orchestrator
- domain directors
- repeated specialist role matrix
- cross-domain councils
- dedicated synthesis agents

The number of Markdown definitions does not determine epistemic independence. Independent tasks, evidence sources, role incentives, and post-hoc synthesis determine effective diversity.

## Filesystem model

The framework repository and workspace are separate roots. CLI commands receive or resolve the workspace path. All execution outputs remain under that workspace.
