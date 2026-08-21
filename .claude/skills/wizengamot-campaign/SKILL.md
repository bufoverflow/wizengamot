---
name: wizengamot-campaign
description: Plan or execute a bounded Agent SDK campaign with structured outputs and explicit budgets.
---

# Campaign execution

1. Preview with `wizengamot --workspace <path> plan --campaign <name>`.
2. Calibrate on three agents.
3. Execute only with `--execute`, `--max-agent-budget`, and `--max-total-budget`.
4. Large runs also require an exact `--ack-large-run <agent-count>` value.
5. Keep source analysis separate from synthesis.
