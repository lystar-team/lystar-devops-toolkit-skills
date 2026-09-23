#!/usr/bin/env python3
"""Shared JSON, schema, hashing, and result helpers for lystar-ui-restore scripts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_ROOT = SKILL_ROOT / "schemas"
WORKFLOW_MODES = {"iteration", "acceptance"}


def resolve_workflow_mode(document: dict[str, Any] | None = None, requested: str | None = None) -> str:
    candidate = requested or (document or {}).get("workflowMode") or "iteration"
    if candidate not in WORKFLOW_MODES:
        raise ContractError(f"workflowMode must be one of: {', '.join(sorted(WORKFLOW_MODES))}")
    return candidate


def normalize_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return value.strip()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment))


def urls_match(left: Any, right: Any) -> bool:
    normalized_left = normalize_url(left)
    normalized_right = normalize_url(right)
    return normalized_left is not None and normalized_left == normalized_right


def close_number(left: Any, right: Any, tolerance: float = 0.01) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def path_within(root: Path, raw: str | Path) -> Path | None:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


class ContractError(ValueError):
    """Raised when an artifact violates a structural or cross-document contract."""


@lru_cache(maxsize=64)
def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_ROOT / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"Schema file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"Invalid schema JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"Schema root must be an object: {path}")
    return value


def validate_document(value: Any, schema_name: str, source: str = "document") -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise ContractError("jsonschema is required by lystar-ui-restore; install requirements.txt") from exc
    validator = Draft202012Validator(load_schema(schema_name))
    errors = sorted(validator.iter_errors(value), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    raise ContractError(f"{source} violates {schema_name} at {location}: {error.message}")


def load_json(path: Path, schema_name: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"Invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    if schema_name:
        validate_document(value, schema_name, str(path))
    return value


def atomic_write_json(path: Path, value: Any, schema_name: str | None = None) -> None:
    if schema_name:
        validate_document(value, schema_name, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scenario_id(document: dict[str, Any] | None) -> str | None:
    if not isinstance(document, dict):
        return None
    value = document.get("scenarioId")
    return value if isinstance(value, str) and value else None


def require_same_scenario(documents: Iterable[tuple[str, dict[str, Any] | None]]) -> str | None:
    values = [(name, scenario_id(document)) for name, document in documents]
    concrete = [(name, value) for name, value in values if value]
    if not concrete:
        return None
    expected = concrete[0][1]
    mismatched = [f"{name}={value}" for name, value in concrete if value != expected]
    if mismatched:
        joined = ", ".join([f"{concrete[0][0]}={expected}", *mismatched])
        raise ContractError(f"scenarioId mismatch: {joined}")
    return expected


def emit(command_status: str, stage_status: str, message: str, exit_code: int, **extra: Any) -> int:
    payload = {
        "commandStatus": command_status,
        "stageStatus": stage_status,
        "message": message,
        "exitCode": exit_code,
        **extra,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code
