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

Reuse the same `--run-id` to resume. Valid successful reports are skipped. Failed, malformed, or report-contract-invalid reports receive a new attempt number. Contract violations are preserved on each attempt and supplied to the next configured retry as repair feedback. Changing the task, campaign, or roster under an existing run ID is rejected.

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
runs/<run-id>/results/
runs/<run-id>/attempts/
```

Do not treat repeated conclusions as independent corroboration when agents share the same project corpus.
