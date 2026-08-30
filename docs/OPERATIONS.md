# Operations

## Preflight

```bash
wizengamot workspace
wizengamot validate
wizengamot count
wizengamot plan --campaign <campaign>
```

Review the selected roster, model distribution, concurrency, waves, retry count, ordered post-source
stages, and the separate source, post-source, and aggregate workflow ceilings.

Before escalating from a calibration to a more expensive qualifier, recompute the saved result contract:

```bash
wizengamot --workspace <workspace> check-run-contract \
  --run-id <calibration-run-id> \
  --name <calibration-agent>
```

Require `mechanically_qualified: true`, then perform semantic review for source scope, evidence classification, and overreach before launching the next model tier.

## Escalation sequence

1. Run one verifier against a known task.
2. Run three heterogeneous agents.
3. Run one domain campaign.
4. Review source quality, truncation, cost, and disagreement.
5. Increase to a large campaign.
6. Let the configured post-source pipeline run, or launch synthesis separately when the campaign has no pipeline.

## Frontier synthesis pipeline

A campaign may declare ordered `post_source_pipeline.stages`. The recommended allocation is narrow,
cheaper models for bounded source work; Opus for local falsification and verification; Fable at
maximum effort for the master synthesis; and an independent Opus challenge at maximum effort.

The original `launch` command authorizes and owns the full workflow. Synthesis starts only when the
exact selected source roster is complete and mechanically qualified. Each stage uses a deterministic
child run ID, so session limits, process interruption, or a manual provider pause resume from the
unfinished stage when the original command is repeated with the same source run ID.

Before the first stage, the runner writes `source-corpus-index.json`: a deterministic projection of
structured findings, citations, risks, falsifiers, recommendations, and evidence gaps with pointers
to every full report. Its source-corpus fingerprint is part of workflow identity. If source results
change after synthesis has started, the runner rejects stale reuse and requires a new run ID.

`--no-post-source` deliberately suppresses configured stages and removes their ceilings from the
plan. Additional campaign selectors also suppress the pipeline because they produce a partial source
roster. Do not use either mechanism merely to bypass an insufficient aggregate authorization.

## Recovery

Reuse the same `--run-id` to resume. Valid successful reports are skipped. Failed, malformed, or report-contract-invalid reports receive a new attempt number. Contract violations are preserved on each attempt and supplied to the first new attempt after resume, as well as to later configured retries. Changing the task, campaign, or roster under an existing run ID is rejected.

Claude session-limit failures are automatically recoverable within the original launch. The runner stops admitting queued agents, drains already-active attempts, writes a durable `waiting-for-session-reset` checkpoint, sleeps until the advertised reset plus a grace interval, and resumes unfinished agents under the same run ID. If the reset hint cannot be parsed or the provider still reports exhaustion, the runner waits and probes again. Completed results are skipped even when the initial command used `--no-skip-existing`.

The final launch result and `summary.json` always report `session_reset_count`; `progress.json`, `pause.json`, and `session-waits.json` expose the count during recovery.

Post-source stages retain their own reset counts. The parent summary reports
`workflow_session_reset_count`, stage costs, and the final challenge result path.

On a TTY, the runner replaces the live activity view with a colored session-reset countdown showing the local resume time and pending work. Redirected logs receive plain `WAITING` and `RESUMING` records with no ANSI escapes. Use `NO_COLOR=1` or `CLICOLOR=0` when an interactive terminal should also remain uncolored.

Account-quota and authentication failures remain manual campaign-wide pauses. They write `pause.json` plus a partial progress and summary record, then exit nonzero with a `Run paused` message. Resolve the provider issue and repeat the launch command with the same run ID. Do not manufacture runner-failure results for agents listed as deferred.

Use `repair-run-contract --run-id <run-id>` to preview deterministic normalization of final
`contract-error` results. The default is strictly read-only. With `--apply`, each accepted result is
first copied into a timestamped `report-contract-backups/` directory, every changed field is audited
in `report_contract_repairs`, and `report-contract-repair-summary.json` records the run-level result.
The command never changes attempt records and rejects a candidate unless it strictly reduces the
error set without adding a new error category. Applied changes invalidate cached corpus hashes and
qualification so post-source synthesis must fingerprint the repaired final results.

Put exceptional recovery model or turn settings in
`runs/<run-id>/recovery-overrides.json`. The versioned file may set only `model`, `max_turns`, and a
reason per agent. Overrides apply only to agents with an existing unsuccessful final result; valid
reports remain untouched. The run summary records configured and applied overrides. Keep this file
inside the private run directory rather than modifying the public registry.

## Large launches

Campaigns selecting at least one hundred agents require:

- `--execute`
- positive `--max-agent-budget`
- sufficient `--max-total-budget`
- exact `--ack-large-run <selected-count>`

Concurrency above fifty also requires `--unsafe-high-concurrency`.

## Operational review

Inspect:

```text
runs/<run-id>/progress.json
runs/<run-id>/summary.json
runs/<run-id>/pause.json
runs/<run-id>/session-waits.json
runs/<run-id>/post-source-pipeline.json
runs/<run-id>/source-corpus-index.json
runs/<run-id>/recovery-overrides.json
runs/<run-id>/report-contract-repair-summary.json
runs/<run-id>/report-contract-backups/
runs/<run-id>/results/
runs/<run-id>/attempts/
runs/<run-id>--post-<position>-<stage>/
```

Do not treat repeated conclusions as independent corroboration when agents share the same project corpus.
