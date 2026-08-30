# Operations

## Preflight

```bash
wizengamot workspace
wizengamot validate
wizengamot count
wizengamot plan --campaign <campaign>
```

Review the selected roster, model distribution, concurrency, waves, retry count, and nominal aggregate ceiling.

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
6. Run synthesis separately.

## Recovery

Reuse the same `--run-id` to resume. Valid successful reports are skipped. Failed, malformed, or report-contract-invalid reports receive a new attempt number. Contract violations are preserved on each attempt and supplied to the first new attempt after resume, as well as to later configured retries. Changing the task, campaign, or roster under an existing run ID is rejected.

Claude session-limit failures are automatically recoverable within the original launch. The runner stops admitting queued agents, drains already-active attempts, writes a durable `waiting-for-session-reset` checkpoint, sleeps until the advertised reset plus a grace interval, and resumes unfinished agents under the same run ID. If the reset hint cannot be parsed or the provider still reports exhaustion, the runner waits and probes again. Completed results are skipped even when the initial command used `--no-skip-existing`.

The final launch result and `summary.json` always report `session_reset_count`; `progress.json`, `pause.json`, and `session-waits.json` expose the count during recovery.

On a TTY, the runner replaces the live activity view with a colored session-reset countdown showing the local resume time and pending work. Redirected logs receive plain `WAITING` and `RESUMING` records with no ANSI escapes. Use `NO_COLOR=1` or `CLICOLOR=0` when an interactive terminal should also remain uncolored.

Account-quota and authentication failures remain manual campaign-wide pauses. They write `pause.json` plus a partial progress and summary record, then exit nonzero with a `Run paused` message. Resolve the provider issue and repeat the launch command with the same run ID. Do not manufacture runner-failure results for agents listed as deferred.

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
runs/<run-id>/results/
runs/<run-id>/attempts/
```

Do not treat repeated conclusions as independent corroboration when agents share the same project corpus.
