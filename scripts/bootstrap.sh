#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
REQUESTED_PYTHON="${PYTHON:-${PYTHON_BIN:-}}"
FORCE_RECREATE=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/bootstrap.sh [--python PATH] [--recreate]

Options:
  --python PATH  Build the virtual environment with this Python executable.
                 The interpreter must be Python 3.11 or newer.
  --recreate     Rebuild .venv even when the existing environment is compatible.
  -h, --help     Show this help text.

Environment overrides:
  PYTHON=PATH       Same as --python PATH.
  PYTHON_BIN=PATH   Fallback alias for PYTHON.
  VENV_DIR=PATH     Use a virtual-environment path other than .venv.
USAGE
}

while (($#)); do
  case "$1" in
    --python)
      if (($# < 2)); then
        printf 'ERROR: --python requires a path.\n' >&2
        exit 2
      fi
      REQUESTED_PYTHON="$2"
      shift 2
      ;;
    --recreate)
      FORCE_RECREATE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# Resolve relative virtual-environment paths against the repository root so
# active-environment filtering and safety checks use a stable absolute path.
if [[ "$VENV_DIR" != /* ]]; then
  VENV_DIR="$ROOT_DIR/${VENV_DIR#./}"
fi

if [[ -z "$VENV_DIR" || "$VENV_DIR" == "/" || "$VENV_DIR" == "$ROOT_DIR" ]]; then
  printf 'ERROR: refusing unsafe VENV_DIR value: %s\n' "$VENV_DIR" >&2
  exit 1
fi

python_is_supported() {
  local executable="$1"
  "$executable" - "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" <<'PY' >/dev/null 2>&1
import sys

required = tuple(map(int, sys.argv[1:3]))
raise SystemExit(0 if sys.version_info[:2] >= required else 1)
PY
}

python_version() {
  local executable="$1"
  "$executable" - <<'PY'
import platform
print(platform.python_version())
PY
}

python_major_minor() {
  local executable="$1"
  "$executable" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
}

# An activated stale .venv can place its old python3 first on PATH. Remove the
# active environment and this project's target environment from interpreter
# discovery so bootstrap selects a base/system interpreter.
build_discovery_path() {
  local original_ifs="$IFS"
  local entry result=""
  local active_bin=""
  local target_bin="${VENV_DIR%/}/bin"

  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    active_bin="${VIRTUAL_ENV%/}/bin"
  fi

  IFS=':'
  for entry in ${PATH:-}; do
    [[ -n "$entry" ]] || continue
    if [[ "$entry" == "$target_bin" || (-n "$active_bin" && "$entry" == "$active_bin") ]]; then
      continue
    fi
    if [[ -z "$result" ]]; then
      result="$entry"
    else
      result="$result:$entry"
    fi
  done
  IFS="$original_ifs"
  printf '%s\n' "$result"
}

DISCOVERY_PATH="$(build_discovery_path)"

resolve_executable() {
  local candidate="$1"

  if [[ "$candidate" == */* ]]; then
    [[ -x "$candidate" ]] || return 1
    printf '%s\n' "$candidate"
    return 0
  fi

  (
    PATH="$DISCOVERY_PATH"
    command -v "$candidate" 2>/dev/null
  )
}

find_supported_python() {
  local candidate resolved
  local -a candidates=(
    python3
    python3.14
    python3.13
    python3.12
    python3.11
    /opt/homebrew/bin/python3
    /opt/homebrew/bin/python3.14
    /opt/homebrew/bin/python3.13
    /opt/homebrew/bin/python3.12
    /opt/homebrew/bin/python3.11
    /usr/local/bin/python3
    /usr/local/bin/python3.14
    /usr/local/bin/python3.13
    /usr/local/bin/python3.12
    /usr/local/bin/python3.11
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
  )

  for candidate in "${candidates[@]}"; do
    resolved="$(resolve_executable "$candidate" || true)"
    [[ -n "$resolved" ]] || continue
    if python_is_supported "$resolved"; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done

  return 1
}

if [[ -n "$REQUESTED_PYTHON" ]]; then
  PYTHON_EXECUTABLE="$(resolve_executable "$REQUESTED_PYTHON" || true)"
  if [[ -z "$PYTHON_EXECUTABLE" ]]; then
    printf 'ERROR: requested Python executable was not found or is not executable: %s\n' "$REQUESTED_PYTHON" >&2
    exit 1
  fi
  if ! python_is_supported "$PYTHON_EXECUTABLE"; then
    REQUESTED_VERSION="$(python_version "$PYTHON_EXECUTABLE" 2>/dev/null || printf 'unknown')"
    printf 'ERROR: %s is Python %s; Python %d.%d or newer is required.\n' \
      "$PYTHON_EXECUTABLE" "$REQUESTED_VERSION" \
      "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" >&2
    exit 1
  fi
else
  PYTHON_EXECUTABLE="$(find_supported_python || true)"
  if [[ -z "$PYTHON_EXECUTABLE" ]]; then
    cat >&2 <<EOF_ERROR
ERROR: no Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ base interpreter was found.

Install a supported Python, then run one of:
  ./scripts/bootstrap.sh --python /absolute/path/to/python3
  PYTHON=/absolute/path/to/python3 ./scripts/bootstrap.sh

On Apple Silicon Homebrew installations, the executable is commonly under /opt/homebrew/bin/.
On Intel Homebrew installations, it is commonly under /usr/local/bin/.
EOF_ERROR
    exit 1
  fi
fi

SELECTED_VERSION="$(python_version "$PYTHON_EXECUTABLE")"
SELECTED_MAJOR_MINOR="$(python_major_minor "$PYTHON_EXECUTABLE")"
printf 'Using Python %s at %s\n' "$SELECTED_VERSION" "$PYTHON_EXECUTABLE"

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  printf 'Ignoring active virtual environment during interpreter selection: %s\n' "$VIRTUAL_ENV"
fi

RECREATE_REASON=""
if ((FORCE_RECREATE)); then
  RECREATE_REASON="--recreate was supplied"
elif [[ -e "$VENV_DIR" ]]; then
  if [[ ! -d "$VENV_DIR" ]]; then
    RECREATE_REASON="the virtual-environment path exists and is not a directory"
  elif [[ ! -x "$VENV_DIR/bin/python" ]]; then
    RECREATE_REASON="the existing virtual environment is incomplete"
  elif ! EXISTING_VERSION="$(python_version "$VENV_DIR/bin/python" 2>/dev/null)"; then
    RECREATE_REASON="the existing virtual environment interpreter is broken"
  elif ! python_is_supported "$VENV_DIR/bin/python"; then
    RECREATE_REASON="the existing virtual environment uses Python $EXISTING_VERSION"
  elif ! EXISTING_MAJOR_MINOR="$(python_major_minor "$VENV_DIR/bin/python" 2>/dev/null)"; then
    RECREATE_REASON="the existing virtual environment interpreter is broken"
  elif [[ "$EXISTING_MAJOR_MINOR" != "$SELECTED_MAJOR_MINOR" ]]; then
    RECREATE_REASON="the existing virtual environment uses Python $EXISTING_MAJOR_MINOR while the selected base interpreter is Python $SELECTED_MAJOR_MINOR"
  fi
fi

if [[ -n "$RECREATE_REASON" ]]; then
  printf 'Recreating %s because %s.\n' "$VENV_DIR" "$RECREATE_REASON"
  rm -rf -- "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_EXECUTABLE" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_WIZENGAMOT="$VENV_DIR/bin/wizengamot"

if ! python_is_supported "$VENV_PYTHON"; then
  printf 'ERROR: virtual environment does not satisfy Python %d.%d+.\n' \
    "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" >&2
  exit 1
fi

printf 'Virtual environment: Python %s at %s\n' "$(python_version "$VENV_PYTHON")" "$VENV_PYTHON"

# Invoke the virtual environment by absolute path instead of relying on shell
# activation. This remains correct when the caller had an older .venv active.
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
"$VENV_PYTHON" -m pip install --editable "$ROOT_DIR"
"$VENV_PYTHON" "$ROOT_DIR/scripts/validate.py"
PYTHONPATH="$ROOT_DIR/src" "$VENV_PYTHON" -m unittest discover -s "$ROOT_DIR/tests" -v
"$VENV_WIZENGAMOT" sdk-check

cat <<EOF_DONE

Installed with Python $(python_version "$VENV_PYTHON"). Try:
  source "$VENV_DIR/bin/activate"
  wizengamot workspace
  wizengamot validate
EOF_DONE
