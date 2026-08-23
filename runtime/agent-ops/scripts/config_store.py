#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def toml_key(value: str) -> str:
    return value if BARE_KEY_RE.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("TOML float must be finite")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        entries = []
        for key, item in value.items():
            if item is not None:
                entries.append(f"{toml_key(str(key))} = {toml_value(item)}")
        return "{" + ", ".join(entries) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def dump_toml(payload: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit_table(table: dict[str, Any], path: tuple[str, ...]) -> None:
        scalars = [(key, value) for key, value in table.items() if not isinstance(value, dict)]
        children = [(key, value) for key, value in table.items() if isinstance(value, dict)]

        if path:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("[" + ".".join(toml_key(part) for part in path) + "]")
        for key, value in scalars:
            if value is not None:
                lines.append(f"{toml_key(str(key))} = {toml_value(value)}")
        for key, value in children:
            emit_table(value, (*path, str(key)))

    emit_table(payload, ())
    return "\n".join(lines).rstrip() + "\n"


def write_toml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = dump_toml(payload)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


FileSnapshot = tuple[bytes, int] | None


def snapshot_files(paths: Iterable[Path]) -> dict[Path, FileSnapshot]:
    snapshots: dict[Path, FileSnapshot] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            snapshots[path] = (path.read_bytes(), path.stat().st_mode & 0o7777)
        else:
            snapshots[path] = None
    return snapshots


def restore_files(snapshots: dict[Path, FileSnapshot]) -> None:
    for path, snapshot in snapshots.items():
        if snapshot is None:
            if path.exists():
                path.unlink()
            continue
        content, mode = snapshot
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.restore.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            os.chmod(temp_name, mode)
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


class FileTransactionError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class FileTransaction:
    """锁定多个配置文件，并在进程内失败时恢复原始字节。"""

    def __init__(
        self,
        paths: Iterable[Path],
        *,
        glob_paths: Iterable[tuple[Path, str]] = (),
    ) -> None:
        self.paths = tuple(sorted({Path(path) for path in paths}, key=str))
        self.glob_paths = tuple((Path(root), pattern) for root, pattern in glob_paths)
        self._lock_context: Any = None
        self._snapshots: dict[Path, FileSnapshot] = {}
        self._committed = False

    def _tracked_paths(self) -> set[Path]:
        paths = set(self.paths)
        for root, pattern in self.glob_paths:
            if root.exists():
                paths.update(path for path in root.glob(pattern) if path.is_file())
        return paths

    def __enter__(self) -> "FileTransaction":
        self._lock_context = lock_files(*self.paths)
        self._lock_context.__enter__()
        self._snapshots = snapshot_files(self._tracked_paths())
        return self

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        current_paths = self._tracked_paths() | set(self._snapshots)
        restore_files({path: self._snapshots.get(path) for path in current_paths})

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        rollback_error: Exception | None = None
        try:
            if exc_type is not None and not self._committed:
                try:
                    self.rollback()
                except Exception as error:
                    rollback_error = error
        finally:
            if self._lock_context is not None:
                self._lock_context.__exit__(exc_type, exc_value, traceback)
        if rollback_error is not None:
            raise FileTransactionError(
                "配置文件写入失败且补偿恢复失败",
                {
                    "status": "inconsistent_state",
                    "rollback_error": str(rollback_error),
                    "original_error": str(exc_value) if exc_value is not None else "",
                    "files": [str(path) for path in sorted(self._tracked_paths(), key=str)],
                },
            ) from rollback_error
        return False


@contextmanager
def lock_files(*paths: Path) -> Iterator[None]:
    handles = []
    try:
        for path in sorted(set(paths), key=str):
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = (path.parent / f".{path.name}.lock").open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
