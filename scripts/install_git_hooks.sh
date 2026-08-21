#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .git ]]; then
  printf 'ERROR: initialize or clone the Git repository before installing hooks.\n' >&2
  exit 1
fi

cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(git rev-parse --show-toplevel)"
exec "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scripts/privacy_check.py" --staged
HOOK
chmod +x .git/hooks/pre-commit
printf 'Installed privacy pre-commit hook.\n'
