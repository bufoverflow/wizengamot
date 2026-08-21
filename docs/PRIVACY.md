# Privacy model

## Principle

Public code and private intelligence occupy different filesystem boundaries. The public archive is constructed from an allowlist. The working tree is never assumed safe.

## Protected material

- project knowledge
- customer and interview records
- founder context
- legal and regulatory drafts
- strategy, pricing, fundraising, and outreach material
- proprietary prompts and claim ledgers
- credentials and connector configuration
- Claude state, memory, and transcripts
- generated reports and evidence exports

## Controls

1. Root `.gitignore` excludes private locations and sensitive file classes.
2. `privacy-policy.local.json` can define project-specific forbidden terms.
3. `scripts/privacy_check.py` scans staged, tracked, explicit, or allowlisted files.
4. `PUBLIC_FILES.json` defines the release boundary.
5. `scripts/build_public_release.py` rejects symlinks and packages allowlisted files only.
6. CI validates the public boundary.

## Failure mode

`.gitignore` has no effect on files already tracked. Remove sensitive paths from the index and purge repository history before publication.
