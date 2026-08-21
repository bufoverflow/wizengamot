#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from privacy_check import scan


def selected_files() -> list[Path]:
    policy = json.loads((ROOT / "PUBLIC_FILES.json").read_text(encoding="utf-8"))
    files: set[Path] = set()
    for raw in policy["include"]:
        path = ROOT / raw
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(p for p in path.rglob("*") if p.is_file())
        else:
            raise FileNotFoundError(f"Public allowlist entry does not exist: {raw}")
    excludes = policy.get("exclude", [])
    selected: list[Path] = []
    for path in sorted(files):
        if path.is_symlink():
            raise ValueError(f"Public release refuses symlink: {path.relative_to(ROOT)}")
        rel = path.relative_to(ROOT).as_posix()
        if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
            continue
        selected.append(path)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a public Wizengamot archive from an explicit allowlist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/wizengamot-public.zip")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output

    try:
        files = selected_files()
        errors = scan(files)
        if errors:
            print(
                f"Public release blocked: {len(errors)} privacy issue(s) detected.",
                file=sys.stderr,
            )
            return 1
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                rel = path.relative_to(ROOT)
                info = zipfile.ZipInfo.from_file(path, arcname=str(Path("wizengamot") / rel))
                info.date_time = (2026, 8, 20, 12, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        checksum = output.with_suffix(output.suffix + ".sha256")
        checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Built {output} from {len(files)} allowlisted files")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
