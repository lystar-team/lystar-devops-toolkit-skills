#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


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
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
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
