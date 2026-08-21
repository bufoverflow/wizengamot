# Epistemic governance

Wizengamot is a reasoning system. It does not manufacture empirical validation.

## Constitutional rule

> Wizengamot may change the set of hypotheses under consideration. Only external observation may change empirical validation.

A report can carry evidence from interviews, project records, experiments, or external sources. The report itself remains model-generated reasoning. Model agreement, model confidence, repeated rediscovery, or failure to falsify a claim never count as independent evidence.

## Evidence contained in a report vs. the report as evidence

Every material finding should distinguish:

- `customer-evidence`: a directly attributable customer or counterparty observation;
- `project-record`: an existing internal record, decision, experiment, or artifact;
- `external-primary`: an authoritative external source such as official documentation, law, regulation, filing, or first-party product documentation;
- `external-secondary`: analysis or reporting derived from other sources;
- `model-reasoning`: inference, hypothesis, recommendation, or generated candidate;
- `unknown`: insufficiently grounded material.

The source evidence contained in a report may be evidence. The report is not evidence.

Model reasoning cannot increase customer-validation, willingness-to-pay, observed-demand, observed-workflow, or traction scores. A primary source surfaced by a model can update a factual claim after the source is checked; the model synthesis does not create a second source.

## Source provenance contract

External citations are atomic. One citation object represents one identifiable source. Do not bundle multiple publications, sites, or documents into one source identifier.

Prefer authoritative primary sources whenever reasonably available for statutes, regulations, filings, standards, protocols, official product capabilities, and first-party claims. Secondary sources may supplement those records but should not substitute for an available primary source.

Each citation records:

- a stable `source_id`;
- `source_class`;
- publisher when known;
- source title or description;
- a locator or URL when available;
- date when known;
- whether it is primary;
- the specific claims it supports.

Each material finding also classifies its claim as `positive`, `negative-capability`, `comparative`, or `other`.

Negative-capability claims require explicit scope accounting. Saying that a system does not support a capability is an inference unless an authoritative source directly establishes that absence. The finding must list the primary documents or specifications actually reviewed in `reviewed_source_ids`, describe the reviewed scope in its evidence, lower confidence when documentation is incomplete, and record the remaining evidence gap. Absence from a marketing page is not evidence of absence.

## Novelty accounting

Each material finding should classify its relationship to the supplied corpus:

- `retrieved`: already explicit in the corpus;
- `derived`: a logical implication of supplied evidence;
- `corroborated`: supported by an additional independent source that supports the same proposition;
- `contradicted`: conflicts with another source or project record;
- `novel`: absent from the reviewed corpus and introduced as a new hypothesis or inference;
- `unresolved`: available evidence does not justify choosing.

Re-deriving an existing observation does not create independent confirmation. Combining facts from multiple sources into a new comparison or conclusion is `derived`, not automatically `corroborated`.

## Evidence reconciliation

Before a material conclusion, agents should identify the relevant records they reviewed, what supports the conclusion, what contradicts it, and what remains unreconciled. When relevant interview or customer evidence exists, falsification and council agents should cite it directly. Missing relevant evidence is an evidence gap.

## Bias to action

Every completed reasoning run should terminate in a concrete next action with an owner and target date.

Match the evidence bar to reversibility:

- reversible, cheap actions such as calls, emails, interviews, and small pilots should usually have a near-zero or low evidence bar;
- partially reversible actions may justify moderate evidence;
- expensive or difficult-to-reverse commitments may justify a high evidence bar.

Agents should explicitly check whether the threshold for action has drifted upward. A higher bar is justified by new external evidence, a customer-raised question, or a real operational constraint. A hypothetical objection generated only by the model does not, by itself, justify raising the bar for a reversible action.

## Analysis-loop guard

Two consecutive reasoning runs that produce no action leaving the machine are a stall signal. Pause further analysis until an external action occurs. Calibration and runtime-debugging runs may be exempt because their purpose is to test the harness rather than advance a decision.

The purpose is simple: analysis that does not terminate in reality becomes a leak, however rigorous.

## Strong uses

Wizengamot is best used for:

- evidence reconciliation;
- falsification and decisive-test design;
- contradiction discovery across domains;
- search-space expansion;
- decision preparation;
- preparation for and analysis of real human discovery.

A healthy loop is:

> Wizengamot expands hypothesis space. Reality contracts hypothesis space.

## Misuses

Do not use agent votes as confidence. Do not treat simulated customers as customer evidence. Do not count repeated model rediscovery as novelty. Do not let model-authored high-stakes founder narrative replace human authorship.

For founder-authored external materials, use agents primarily as adversarial reviewers: audit claims, evidence, ambiguity, confidentiality, likely objections, and compression while preserving human authorship.
