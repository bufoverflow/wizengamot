#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PARTS = {
    "workspace",
    "private",
    ".private",
    "knowledge",
    "customer-data",
    "attachments",
    "uploads",
    "transcripts",
}
RUNTIME_PARTS = {"runs", "work", "artifacts", "reports", "logs", "traces"}
KEY_SUFFIXES = {
    ".pem", ".key", ".p8", ".p12", ".pfx", ".jks", ".keystore",
    ".crt", ".cer", ".der", ".csr", ".mobileprovision", ".kdbx",
    ".age", ".gpg",
}
PRIVATE_SUFFIXES = (
    ".private.md", ".private.json", ".private.toml",
    ".confidential.md", ".confidential.json",
    ".sensitive.md", ".sensitive.json", ".internal.md",
)
LOCAL_SECRET_NAMES = {
    ".mcp.json", ".mcp.local.json", "settings.local.json", "wizengamot.local.toml",
    ".claude.json", ".claude.json.backup", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "auth.json", "token.json", "tokens.json", "cookies.json",
    "session.json",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
    "Anthropic API key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    "non-empty ANTHROPIC_API_KEY": re.compile(r"(?m)^\s*ANTHROPIC_API_KEY\s*=\s*[^\s#]+"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
}


def git_files(mode: str) -> list[Path]:
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        cmd = ["git", "ls-files"]
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return [ROOT / line for line in result.stdout.splitlines() if line.strip()]


def load_public_files() -> list[Path]:
    policy = json.loads((ROOT / "PUBLIC_FILES.json").read_text(encoding="utf-8"))
    includes = policy["include"]
    excludes = policy.get("exclude", [])
    files: set[Path] = set()
    for raw in includes:
        path = ROOT / raw
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(p for p in path.rglob("*") if p.is_file())
    result = []
    for path in sorted(files):
        rel = path.relative_to(ROOT).as_posix()
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        result.append(path)
    return result


def load_forbidden_terms() -> list[str]:
    path = ROOT / "privacy-policy.local.json"
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    terms = value.get("forbidden_terms", []) if isinstance(value, dict) else []
    return [str(term) for term in terms if str(term).strip()]


def path_errors(path: Path) -> list[str]:
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return [f"path escapes repository: {path}"]
    parts = set(rel.parts)
    name = rel.name
    errors: list[str] = []
    if parts & FORBIDDEN_PARTS:
        errors.append("private workspace or knowledge path")
    if parts & RUNTIME_PARTS and name != ".gitkeep":
        errors.append("runtime output path")
    if name in LOCAL_SECRET_NAMES or name.startswith("oauth"):
        errors.append("local authentication or configuration path")
    if name.casefold().endswith(PRIVATE_SUFFIXES):
        errors.append("private document filename")
    if name.startswith(".env") and name != ".env.example":
        errors.append("environment file")
    if path.suffix.lower() in KEY_SUFFIXES:
        errors.append("key or certificate material")
    if name.startswith(("id_rsa", "id_ed25519")):
        errors.append("SSH key material")
    return errors


def read_staged_bytes(path: Path) -> bytes:
    """Read the exact blob currently staged in Git's index."""
    rel = path.relative_to(ROOT).as_posix()
    result = subprocess.run(
        ["git", "show", f":{rel}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not read staged content for {rel}"
        )
    return result.stdout


def scan(paths: list[Path], *, staged: bool = False) -> list[str]:
    errors: list[str] = []
    terms = load_forbidden_terms()

    for path in paths:
        rel = path.relative_to(ROOT).as_posix()

        for issue in path_errors(path):
            errors.append(f"{rel}: {issue}")

        if staged:
            data = read_staged_bytes(path)
        else:
            if not path.exists() or not path.is_file():
                continue
            data = path.read_bytes()

        if len(data) > 5_000_000:
            errors.append(f"{rel}: file exceeds 5 MB public review limit")
            continue

        if b"\x00" in data:
            continue

        file_text = data.decode("utf-8", errors="replace")

        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(file_text):
                errors.append(f"{rel}: possible {label}")

        lower = file_text.casefold()
        for term in terms:
            if term.casefold() in lower:
                errors.append(
                    f"{rel}: contains locally forbidden content"
                )

    return sorted(set(errors))

def main() -> int:
    parser = argparse.ArgumentParser(description="Reject private paths and likely secrets from the public boundary")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="Scan staged Git files")
    group.add_argument("--tracked", action="store_true", help="Scan all tracked Git files")
    group.add_argument("--all-public", action="store_true", help="Scan the public release allowlist")
    group.add_argument("--paths", nargs="+", help="Scan explicit paths relative to the repository")
    args = parser.parse_args()

    try:
        if args.staged:
            paths = git_files("staged")
        elif args.tracked:
            paths = git_files("tracked")
        elif args.all_public:
            paths = load_public_files()
        else:
            paths = [(ROOT / value).resolve() for value in args.paths]
        errors = scan(paths, staged=args.staged)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Privacy check failed to run: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Privacy check passed for {len(paths)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
