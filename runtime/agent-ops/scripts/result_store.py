#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from config_store import lock_files


TTL_SECONDS = 72 * 60 * 60
MAX_TOTAL_BYTES = 50 * 1024 * 1024
SESSION_ENV = (
    ("AGENT_SESSION_ID", "agent"),
    ("PI_SESSION_ID", "pi"),
    ("CODEX_THREAD_ID", "codex"),
)


def results_root() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "agent-ops" / "results"


def session_identity() -> tuple[str, str] | None:
    for variable, agent in SESSION_ENV:
        value = os.environ.get(variable, "").strip()
        if value:
            digest = hashlib.sha256(value.encode()).hexdigest()[:20]
            return agent, f"{agent}-{digest}"
    return None


def canonical_project_root(value: str | Path | None = None) -> Path:
    current = Path(value or Path.cwd()).expanduser().resolve()
    if value is not None:
        return current
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def project_key(project_root: Path) -> str:
    return hashlib.sha256(str(project_root).encode()).hexdigest()[:16]


def session_dir(project_root: Path) -> Path | None:
    identity = session_identity()
    if identity is None:
        return None
    return results_root() / project_key(project_root) / identity[1]


def snapshot_path(tool: str, project_root: Path) -> Path | None:
    directory = session_dir(project_root)
    return directory / f"{tool}-last.json" if directory else None


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def save_snapshot(
    tool: str,
    action: str,
    project_root: Path,
    request: dict[str, Any],
    result: dict[str, Any],
    success: bool,
    target: str = "",
) -> Path | None:
    identity = session_identity()
    path = snapshot_path(tool, project_root)
    if identity is None or path is None:
        return None
    payload = {
        "version": 1,
        "agent": identity[0],
        "session_id": os.environ[next(name for name, agent in SESSION_ENV if agent == identity[0])],
        "project_root": str(project_root),
        "tool": tool,
        "action": action,
        "target": target,
        "executed_at": utc_now(),
        "success": success,
        "request": request,
        "result": result,
    }
    lock_target = results_root() / "state"
    try:
        with lock_files(lock_target):
            write_json(path, payload)
            os.utime(path.parent, None)
    except OSError:
        return None
    cleanup()
    return path


def load_snapshot(tool: str, project_root: Path) -> dict[str, Any]:
    path = snapshot_path(tool, project_root)
    if path is None:
        raise RuntimeError("result snapshots need AGENT_SESSION_ID, PI_SESSION_ID, or CODEX_THREAD_ID")
    lock_target = results_root() / "state"
    with lock_files(lock_target):
        if not path.is_file():
            raise FileNotFoundError(f"no saved {tool} result for this project session")
        payload = json.loads(path.read_text(encoding="utf-8"))
        os.utime(path.parent, None)
    return payload


def clear_snapshot(tool: str, project_root: Path) -> bool:
    path = snapshot_path(tool, project_root)
    if path is None:
        raise RuntimeError("result snapshots need AGENT_SESSION_ID, PI_SESSION_ID, or CODEX_THREAD_ID")
    lock_target = results_root() / "state"
    with lock_files(lock_target):
        existed = path.exists()
        path.unlink(missing_ok=True)
        remove_empty_parents(path.parent)
    return existed


def remove_empty_parents(directory: Path) -> None:
    root = results_root()
    while directory != root:
        try:
            directory.rmdir()
        except OSError:
            break
        directory = directory.parent


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = snapshot.get("result", {})
    summary: dict[str, Any] = {
        "action": snapshot.get("action", ""),
        "target": snapshot.get("target", ""),
        "executed_at": snapshot.get("executed_at", ""),
        "success": bool(snapshot.get("success")),
    }
    if "error" in result:
        summary["error"] = result["error"]
    for key in ("exit_code", "has_more", "cells_truncated", "statement_count", "affected_rows", "files", "bytes", "job_id"):
        if key in result:
            summary[key] = result[key]
    if "columns" in result:
        summary["columns"] = result["columns"]
    if isinstance(result.get("rows"), list):
        summary["rows"] = len(result["rows"])
    for key in ("stdout", "stderr", "content"):
        if isinstance(result.get(key), str):
            summary[f"{key}_chars"] = len(result[key])
    status = result.get("status")
    if isinstance(status, dict):
        summary["status"] = status
    return summary


def snapshot_view(snapshot: dict[str, Any], summary: bool) -> dict[str, Any]:
    if summary:
        return snapshot_summary(snapshot)
    return {
        "action": snapshot.get("action", ""),
        "target": snapshot.get("target", ""),
        "executed_at": snapshot.get("executed_at", ""),
        "success": bool(snapshot.get("success")),
        "request": snapshot.get("request", {}),
        "result": snapshot.get("result", {}),
    }


def cleanup(now: float | None = None, ttl_seconds: int = TTL_SECONDS, max_bytes: int = MAX_TOTAL_BYTES) -> None:
    root = results_root()
    if not root.exists():
        return
    lock_target = root / "state"
    try:
        with lock_files(lock_target):
            current_time = now if now is not None else time.time()
            sessions = [path for project in root.iterdir() if project.is_dir() for path in project.iterdir() if path.is_dir()]
            for directory in sessions:
                if current_time - directory.stat().st_mtime > ttl_seconds:
                    shutil.rmtree(directory, ignore_errors=True)
            sessions = [path for project in root.iterdir() if project.is_dir() for path in project.iterdir() if path.is_dir()]
            sizes = {directory: sum(path.stat().st_size for path in directory.glob("*-last.json")) for directory in sessions}
            total = sum(sizes.values())
            for directory in sorted(sessions, key=lambda path: path.stat().st_mtime):
                if total <= max_bytes:
                    break
                total -= sizes[directory]
                shutil.rmtree(directory, ignore_errors=True)
            for project in [path for path in root.iterdir() if path.is_dir()]:
                try:
                    project.rmdir()
                except OSError:
                    pass
    except OSError:
        return
