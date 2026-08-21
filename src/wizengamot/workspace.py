from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from .models import WorkspaceConfig

WORKSPACE_MARKER = "wizengamot.workspace.json"
LOCAL_CONFIG = "wizengamot.local.toml"


def repository_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src/wizengamot").is_dir():
            return candidate
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "pyproject.toml").exists():
        return package_root
    raise FileNotFoundError("Could not locate the Wizengamot repository root")


def _resolve_path(value: str | os.PathLike[str], *, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _validate_workspace(path: Path) -> Path:
    marker = path / WORKSPACE_MARKER
    registry = path / "registry/agents.json"
    if not marker.exists() or not registry.exists():
        raise FileNotFoundError(
            f"Invalid Wizengamot workspace {path}: expected {WORKSPACE_MARKER} and registry/agents.json"
        )
    return path


def _nearest_workspace(start: Path) -> Path | None:
    here = start.resolve()
    for candidate in (here, *here.parents):
        if (candidate / WORKSPACE_MARKER).exists():
            return candidate
    return None


def _workspace_from_local_config(repo: Path) -> Path | None:
    path = repo / LOCAL_CONFIG
    if not path.exists():
        return None
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    workspace = value.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError(f"{path} must define a non-empty workspace string")
    return _resolve_path(workspace, base=repo)


def resolve_workspace(
    explicit: Path | str | None = None,
    *,
    start: Path | None = None,
) -> Path:
    cwd = (start or Path.cwd()).resolve()
    if explicit is not None:
        return _validate_workspace(_resolve_path(explicit, base=cwd))

    env_workspace = os.getenv("WIZENGAMOT_WORKSPACE")
    if env_workspace:
        return _validate_workspace(_resolve_path(env_workspace, base=cwd))

    nearest = _nearest_workspace(cwd)
    if nearest is not None:
        return _validate_workspace(nearest)

    repo = repository_root(cwd)
    configured = _workspace_from_local_config(repo)
    if configured is not None:
        return _validate_workspace(configured)

    fallback = repo / "examples/atlas"
    return _validate_workspace(fallback)


def load_workspace_config(root: Path) -> WorkspaceConfig:
    value = json.loads((root / WORKSPACE_MARKER).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{root / WORKSPACE_MARKER} must contain a JSON object")
    return WorkspaceConfig.from_dict(value)
