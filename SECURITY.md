# Security policy

## Reporting

Do not open public issues containing credentials, private prompts, customer material, legal documents, model transcripts, or proprietary project context. Use a private maintainer channel for security reports.

## Repository boundary

The public repository contains the orchestration engine, schemas, tests, documentation, and a fictional example workspace. Local workspaces and knowledge corpora are excluded through `.gitignore`, staged-file checks, and the public-release builder.

Run before every commit:

```bash
python scripts/privacy_check.py --staged
```

Run before every release:

```bash
python scripts/build_public_release.py --output dist/wizengamot-public.zip
```

The release command packages the public allowlist rather than the working tree.
