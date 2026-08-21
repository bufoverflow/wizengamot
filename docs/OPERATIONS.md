# Operations

## Preflight

```bash
wizengamot workspace
wizengamot validate
wizengamot count
wizengamot plan --campaign <campaign>
```

Review the selected roster, model distribution, concurrency, waves, retry count, and nominal aggregate ceiling.

## Escalation sequence

1. Run one verifier against a known task.
2. Run three heterogeneous agents.
3. Run one domain campaign.
4. Review source quality, truncation, cost, and disagreement.
5. Increase to a large campaign.
6. Run synthesis separately.

## Recovery

Reuse the same `--run-id` to resume. Valid successful reports are skipped. Failed or malformed reports receive a new attempt number. Changing the task, campaign, or roster under an existing run ID is rejected.

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
