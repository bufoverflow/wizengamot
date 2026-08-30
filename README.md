# Wizengamot

**Privacy-first multi-agent orchestration for Claude Code and the Claude Agent SDK.**

Wizengamot turns a project into a governed council of independent agents: directors decompose work, specialists investigate narrow questions, adversarial councils attack assumptions, verifiers audit evidence, and synthesis agents reconcile completed reports. The framework supports interactive Claude Code subagents and bounded batch campaigns ranging from one worker to several hundred independent Agent SDK sessions.

The name follows the repository's Harry Potter naming convention. A Wizengamot is a council. This implementation uses the same organizing idea: many bounded voices, explicit authority, preserved dissent, and one reviewable decision record.

> **Public/private boundary:** framework code, schemas, tests, documentation, and the fictional example are public. Local workspaces, every `knowledge/` directory, private prompts, local Claude state, credentials, model transcripts, and generated reports are ignored and excluded from public releases.

## Core properties

- **Independent source passes.** Each batch worker starts a fresh Agent SDK session and receives a narrow identity, task, context set, tool boundary, turn limit, and budget.
- **Adversarial role design.** Constructive agents are paired with falsifiers, substitute analysts, risk analysts, customer perspectives, and verifiers.
- **Controlled large runs.** Dry-run planning, bounded concurrency, per-agent ceilings, aggregate authorization, and an exact large-run acknowledgement prevent accidental launches.
- **Structured results.** Every worker returns a JSON-schema-constrained report with findings, confidence, risks, falsifiers, evidence gaps, citations, recommendations, and handoffs.
- **Restartable execution.** Atomic files, attempt history, stable run IDs, successful-result skipping, and explicit retries make campaigns resumable.
- **Private overlays.** The engine stays domain-neutral. Project-specific knowledge, agent prompts, claims, campaigns, and synthesis instructions live in an ignored workspace.
- **Release containment.** Public archives are built from an allowlist. The working tree is never packaged directly.

## Repository model

```text
wizengamot/
├── src/wizengamot/            # Public orchestration engine
├── schemas/                   # Public structured-output schema
├── scripts/                   # Bootstrap, privacy check, release builder
├── tests/                     # Public deterministic tests
├── docs/                      # Public architecture and operations
├── examples/atlas/            # Fictional public workspace
├── .claude/                   # Public framework agent and skills
├── workspace/                 # Local private workspace; gitignored
├── knowledge/                 # Local private corpus; gitignored everywhere
├── wizengamot.local.toml      # Local workspace pointer; gitignored
└── README.md
```

A workspace contains its own agent registry, agent definitions, campaigns, tasks, context, output schema, Claude configuration, and run directory:

```text
<workspace>/
├── wizengamot.workspace.json
├── CLAUDE.md
├── registry/agents.json
├── agent-library/
├── campaigns/
├── tasks/
├── context/ or knowledge/
├── schemas/agent-result.schema.json
├── .claude/agents/
└── runs/
```

## Requirements

- Python 3.11 or newer
- Bash for the bootstrap script
- Claude authentication for live sessions
- Network access when installing the Agent SDK or conducting web research

The repository pins `claude-agent-sdk>=0.2.143,<0.3`. The bootstrap script creates a virtual environment and repairs an existing environment that still points to an older Python interpreter.

## Installation

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

The bootstrap process:

1. Ignores an activated stale virtual environment during interpreter discovery.
2. Finds a Python 3.11+ base interpreter through `PATH`, Homebrew, and common python.org locations.
3. Recreates `.venv` when its interpreter is unsupported, broken, incomplete, or from a different Python major/minor release.
4. Installs Wizengamot and the pinned Claude Agent SDK.
5. Validates the resolved workspace.
6. Runs the unit tests.
7. Constructs bounded SDK options without making a model call.

Select a Python explicitly:

```bash
./scripts/bootstrap.sh \
  --python /opt/homebrew/bin/python3.13 \
  --recreate
```

## Workspace resolution

Wizengamot resolves a workspace in this order:

1. `--workspace <path>`
2. `WIZENGAMOT_WORKSPACE`
3. The nearest parent containing `wizengamot.workspace.json`
4. `workspace` from `wizengamot.local.toml`
5. `examples/atlas`

Inspect the selected workspace:

```bash
wizengamot workspace
```

A private local setup uses the ignored configuration file:

```bash
cp wizengamot.local.toml.example wizengamot.local.toml
```

```toml
workspace = "workspace"
```

## Public example

Validate the fictional Atlas workspace:

```bash
wizengamot --workspace examples/atlas validate
wizengamot --workspace examples/atlas count
wizengamot --workspace examples/atlas list
wizengamot --workspace examples/atlas plan --campaign full-audit
```

Atlas contains seventeen agents:

- one chief
- two domain directors
- twelve specialists
- one adversarial council
- one synthesis agent

Its source audit selects fifteen independent workers. No paid session starts during `plan`.
The example campaign also declares a bounded two-stage post-source pipeline: Fable performs the
master synthesis at maximum effort, then Opus independently challenges it at maximum effort.

## Interactive Claude Code use

Run Claude Code from the selected workspace so its project-scoped agents and instructions are discovered:

```bash
cd examples/atlas
claude --agent atlas-chief
```

For a private workspace:

```bash
cd workspace
claude --agent wizengamot-chief
```

Activate a specialist that is stored outside the default interactive roster:

```bash
wizengamot --workspace workspace activate \
  --domain <domain-slug> \
  --role falsification-agent
```

Activate a named council or synthesizer:

```bash
wizengamot --workspace workspace activate \
  --name <agent-name>
```

Clear activated definitions:

```bash
wizengamot --workspace workspace activate --clear
```

Activation above fifty definitions requires `--unsafe-large-activation`. Large investigations belong in the batch runner.

## Batch campaigns

### 1. Inspect the roster

```bash
wizengamot --workspace workspace list \
  --domain <domain-slug> \
  --role falsification-agent
```

### 2. Plan without spending

```bash
wizengamot --workspace workspace plan \
  --campaign <campaign-name> \
  --concurrency 8 \
  --max-agent-budget 0.50
```

The plan reports:

- selected agent count
- tier and model distribution
- concurrency
- execution waves
- per-agent ceiling
- retry count
- source and post-source budget ceilings
- ordered post-source models, effort, turns, and retries
- nominal aggregate workflow ceiling
- whether a large-run acknowledgement is required

### 3. Calibrate on a small set

```bash
wizengamot --workspace workspace launch \
  --name <first-agent> \
  --name <second-agent> \
  --name <verifier-agent> \
  --task "Evaluate the decision, attack the strongest assumption, and return decisive next tests." \
  --concurrency 3 \
  --max-agent-budget 0.50 \
  --max-total-budget 1.50
```

The command remains a dry run. Add `--execute` only after inspecting the roster and arithmetic.

### 4. Execute a bounded small run

```bash
wizengamot --workspace workspace launch \
  --name <first-agent> \
  --name <second-agent> \
  --name <verifier-agent> \
  --task "Evaluate the decision, attack the strongest assumption, and return decisive next tests." \
  --run-id calibration-001 \
  --concurrency 3 \
  --max-agent-budget 0.50 \
  --max-total-budget 1.50 \
  --execute
```

### 5. Execute a large campaign

A campaign selecting one hundred or more workers requires an exact acknowledgement equal to the selected agent count:

```bash
wizengamot --workspace workspace launch \
  --campaign <large-campaign> \
  --run-id audit-001 \
  --concurrency 12 \
  --max-agent-budget 0.50 \
  --max-total-budget <authorized-total> \
  --ack-large-run <exact-agent-count> \
  --execute
```

The aggregate authorization uses this deterministic pre-launch calculation:

```text
source agents × source ceiling × (source retries + 1)
+ Σ(post-source stage ceiling × (stage retries + 1))
```

Configured post-source stages are included in the original authorization and run automatically
only after every selected source report passes the runtime contract. Use `--no-post-source` for a
deliberate source-only launch. Narrowing a campaign with additional selectors also disables its
post-source pipeline so a partial calibration cannot masquerade as a complete campaign synthesis.

The value is a local launch gate. Provider billing, rate limits, final-turn cost, model availability, and account-level limits remain external.

## Result storage

A run produces:

```text
<workspace>/runs/<run-id>/
├── manifest.json
├── progress.json
├── summary.json
├── pause.json                 # present after a global provider pause
├── session-waits.json         # present after automatic session-limit waits
├── post-source-pipeline.json  # configured stage definitions and current workflow state
├── source-corpus-index.json   # deterministic structured index with full-report pointers
├── recovery-overrides.json    # optional private, run-local model/turn recovery settings
├── report-contract-repair-summary.json  # present after an applied deterministic repair
├── report-contract-backups/   # timestamped originals from applied repairs
├── results/
│   └── <agent-name>.json
└── attempts/
    └── <agent-name>/
        ├── attempt-1.json
        └── attempt-2.json
```

Behavior:

- Each result write is atomic.
- Existing successful reports are skipped by default.
- Failed, malformed, or report-contract-invalid reports remain saved and eligible for a later attempt.
- Provenance violations are stored in `report_contract_errors`, prevent success, and are supplied to the next configured retry as repair feedback.
- Saved provenance violations are restored on the first new attempt after resuming a contract-invalid result.
- Claude session limits checkpoint the run without consuming runner retries. In-flight attempts drain and persist, queued agents remain deferred with no result file, and the original launch waits for the advertised reset before resuming unfinished work automatically.
- Reset hints with relative durations, ISO timestamps, or local clock times are honored with a small grace window. Unparseable hints use a conservative polling interval; repeated session-limit responses re-enter the wait loop.
- Session waits are recorded in `session-waits.json`, while `progress.json`, `summary.json`, and `pause.json` expose the current waiting state. The final CLI result and persisted summary always include `session_reset_count` (including zero). The manifest still records one user launch regardless of internal recovery passes.
- Interactive terminals use color-coded live activity and result statuses. During a session-limit wait, a prominent countdown shows the checkpointed run, local resume time, recovery count, and completed/retrying/deferred totals.
- Redirected output remains ANSI-free and receives durable `WAITING` and `RESUMING` lines. Set `NO_COLOR` or `CLICOLOR=0` to disable terminal colors.
- Exhausted account quota and authentication failures remain manual pauses because they do not provide a reliable automatic recovery point.
- Reusing a run ID with a different task, campaign, or roster is rejected.
- Run IDs reject path traversal.
- Retries default to zero.
- Known SDK costs are aggregated from attempt records.

Recompute the current mechanical report contract before promoting a calibration or qualifier:

```bash
wizengamot --workspace workspace check-run-contract \
  --run-id <run-id> \
  --name <agent-name>
```

The command is read-only and exits nonzero when a selected result is missing, unsuccessful, malformed, or provenance-invalid. A mechanical pass does not replace semantic or human review.

For an existing run, plan deterministic repairs without changing any file:

```bash
wizengamot --workspace workspace repair-run-contract \
  --run-id <run-id>
```

Add `--apply` only after reviewing the plan. The normalizer considers final `contract-error`
results only. It can canonicalize direct citation locators, derive stable external IDs, merge exact
direct-URL/DOI duplicates, copy already-cited source IDs into missing reviewed-source accounting,
and conservatively downgrade unsupported novelty or evidence classifications. It never invents
evidence or edits `attempt-*.json`. An applied report is backed up and replaced only when its
recomputed error set is strictly smaller and introduces no new error category. Each field change is
recorded in `report_contract_repairs`, and the run-level summary preserves unresolved patterns.

Applied repairs invalidate cached source-corpus qualification and hashes. The post-source pipeline
therefore fingerprints the final repaired corpus rather than reusing stale synthesis inputs.

Targeted recovery tuning belongs in the ignored run directory, not the canonical agent registry:

```json
{
  "version": 1,
  "run_id": "audit-001",
  "overrides": {
    "example-writer-translator": {
      "model": "sonnet",
      "max_turns": 28,
      "reason": "failed writer recovery"
    }
  }
}
```

Save this as `runs/<run-id>/recovery-overrides.json`. Overrides are validated at launch and apply
only when that agent already has an unsuccessful final result. Qualified results are skipped
unchanged, and the summary records every override actually used.

## Source analysis and synthesis

Source workers execute before synthesis. This ordering limits conclusion leakage and correlated reasoning.

When a campaign defines `post_source_pipeline.stages`, the original launch automatically:

1. finishes or resumes the source roster;
2. checks the exact source roster for contract-valid reports;
3. fingerprints the qualified result set and builds a deterministic structured corpus index;
4. runs each synthesis or challenge stage sequentially under a deterministic child run ID;
5. preserves stage attempts, costs, contract feedback, and session-reset recovery independently; and
6. writes the combined state to `post-source-pipeline.json` and the parent `summary.json`.

Example campaign configuration:

```json
{
  "post_source_pipeline": {
    "stages": [
      {
        "name": "frontier-master-synthesis",
        "agent": "atlas-synthesis-decision-memo",
        "model": "claude-fable-5",
        "effort": "max",
        "max_turns": 36,
        "budget_usd": 10.0,
        "retries": 1,
        "task": "Reconcile the qualified corpus into one traceable decision."
      },
      {
        "name": "independent-opus-challenge",
        "agent": "atlas-council-adversarial-review",
        "model": "claude-opus-5",
        "effort": "max",
        "max_turns": 32,
        "budget_usd": 5.0,
        "retries": 1,
        "task": "Challenge the frontier synthesis against the qualified corpus."
      }
    ]
  }
}
```

The second and later stages receive the preceding stage result paths in addition to the complete
qualified source directory. Frontier synthesis begins from `source-corpus-index.json`, which retains
structured decision fields and full-report paths without adding another model-generated summary.
The source fingerprint is part of pipeline identity, so a changed report cannot silently reuse stale
synthesis. Mechanical qualification gates synthesis but does not turn source reports—or model
agreement—into empirical evidence.

Campaigns without a configured pipeline can still run synthesis manually:

```bash
wizengamot --workspace workspace launch \
  --name <synthesis-agent> \
  --task "Read runs/audit-001/results. Reconcile claims, preserve dissent, and produce a traceable decision artifact." \
  --run-id synthesis-001 \
  --concurrency 1 \
  --max-agent-budget 2.00 \
  --max-total-budget 2.00 \
  --execute
```

Synthesis reports may populate:

- `artifact_markdown`
- `decision_log`
- `source_report_paths`

## Batch permission boundary

Default batch workers receive:

```text
Read
Grep
Glob
WebSearch
WebFetch
```

They are explicitly denied:

```text
Agent
Bash
Edit
Write
NotebookEdit
TaskCreate
TaskUpdate
TaskStop
```

The runner also disables built-in agents, prompt-history persistence, automatic memory, session forking, and nested subagent concurrency. Batch workers can research and analyze. They cannot mutate project files or delegate.

## Workspace contract

`wizengamot.workspace.json` defines the validation contract:

```json
{
  "name": "example",
  "display_name": "Example Council",
  "description": "A project-specific agent workspace.",
  "expected_counts": {
    "chief": 1,
    "director": 2,
    "specialist": 12,
    "council": 1,
    "synthesis": 1
  },
  "default_interactive_count": 3,
  "audit_campaign": "full-audit",
  "audit_worker_count": 15,
  "output_schema": "schemas/agent-result.schema.json",
  "instruction_files": ["CLAUDE.md"],
  "required_context": ["context/INDEX.md"]
}
```

Agent records define identity, tier, domain, role, model, turn limit, budget recommendation, tools, prompt path, interactive path, and relevant context:

```json
{
  "name": "example-reliability-falsification-agent",
  "title": "Reliability Falsification Agent",
  "tier": "specialist",
  "domain": "reliability",
  "role": "falsification-agent",
  "description": "Attacks the strongest reliability claim.",
  "model": "opus",
  "max_turns": 24,
  "recommended_budget_usd": 0.75,
  "effort": "xhigh",
  "tools": ["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
  "disallowed_tools": ["Agent", "Bash", "Edit", "Write"],
  "library_path": "agent-library/specialists/reliability/example-reliability-falsification-agent.md",
  "interactive_path": null,
  "context": ["context/INDEX.md", "context/claims.json"]
}
```

## Privacy boundary

### Ignored by default

- `/workspace/`
- every directory named `knowledge/`
- private overlays
- `wizengamot.local.toml`
- local privacy policies
- activated private agents
- Claude project state and memory
- MCP configuration
- credentials and key material
- runs, reports, artifacts, traces, and transcripts
- uploads, attachments, and private evidence
- local databases and cloud state

`.gitignore` protects untracked files. It does not erase files that were already committed. Remove previously tracked sensitive files from the index and history before publishing.

### Commit-time check

Initialize the hook after creating a Git repository:

```bash
./scripts/install_git_hooks.sh
```

Run manually:

```bash
python scripts/privacy_check.py --staged
python scripts/privacy_check.py --tracked
python scripts/privacy_check.py --all-public
```

The scanner rejects private paths, local configuration, likely credentials, private keys, oversized files, and locally configured forbidden terms.

### Public release

Build a public archive from the explicit allowlist:

```bash
python scripts/build_public_release.py \
  --output dist/wizengamot-public.zip
```

The builder:

1. Reads `PUBLIC_FILES.json`.
2. Includes only allowlisted files.
3. Rejects symlinks.
4. runs the privacy scanner.
5. creates a deterministic ZIP.
6. writes a SHA-256 checksum.

The local `workspace/` can exist in the same source tree. It never enters the public archive.

## Verification

```bash
make verify
```

Equivalent commands:

```bash
wizengamot --workspace examples/atlas validate
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/privacy_check.py --all-public
wizengamot --workspace examples/atlas plan --campaign full-audit
```

No paid model call occurs during validation, unit tests, planning, privacy checks, or static SDK option construction.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- [`docs/PRIVACY.md`](docs/PRIVACY.md)
- [`docs/WORKSPACE_FORMAT.md`](docs/WORKSPACE_FORMAT.md)
- [`examples/atlas/AGENTS.md`](examples/atlas/AGENTS.md)

## Status

Wizengamot is alpha software. Human review remains the final gate for project decisions, canonical updates, external publication, and any action carrying legal, financial, operational, or security consequences.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).

Wizengamot is an independent project. It is not affiliated with or endorsed by the owners of the Harry Potter franchise. See [`NOTICE.md`](NOTICE.md).
