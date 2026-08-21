#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'ERROR: initialize or clone the Git repository before installing hooks.\n' >&2
  exit 1
fi

HOOKS_DIR="$(git rev-parse --git-path hooks)"
mkdir -p "$HOOKS_DIR"

cat > "$HOOKS_DIR/pre-commit" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(git rev-parse --show-toplevel)"
exec "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scripts/privacy_check.py" --staged
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
printf 'Installed privacy pre-commit hook at %s.\n' "$HOOKS_DIR/pre-commit"
