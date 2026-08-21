# Contributing

1. Keep the framework domain-neutral. Put proprietary prompts, customer data, internal strategy, and project knowledge in an ignored local workspace.
2. Add tests for every change to selection, validation, launch controls, retries, resume behavior, or privacy boundaries.
3. Run `make verify` before opening a pull request.
4. Keep paid execution opt-in. A launch must remain a dry run until the operator supplies `--execute`, a per-agent ceiling, and an aggregate authorization.
5. Preserve independent source analysis. Synthesis should consume completed reports rather than share conclusions into the source pass.
6. Avoid dependencies unless they materially improve correctness or containment.
