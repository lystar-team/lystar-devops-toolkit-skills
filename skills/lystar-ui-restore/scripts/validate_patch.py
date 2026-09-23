#!/usr/bin/env python3
"""Validate a scoped patch against schemas, scenario evidence, and file boundaries."""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
from typing import Any

from restore_core import ContractError, load_json, require_same_scenario, sha256_file, validate_document


def emit(status: str, message: str, code: int, **extra: Any) -> int:
    print(json.dumps({"status": status, "message": message, **extra}, ensure_ascii=False))
    return code


def load(path: Path) -> dict[str, Any]:
    return load_json(path)


def matches(path: str, patterns: list[str]) -> bool:
    normalized = path.lstrip("./")
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def safe_file(root: Path, raw_path: Any) -> tuple[str, Path] | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return raw_path, candidate


def validate_patch_data(
    patch: dict[str, Any],
    adapter: dict[str, Any],
    root: Path,
    check_hash: bool = False,
    regions_doc: dict[str, Any] | None = None,
    evidence_doc: dict[str, Any] | None = None,
) -> tuple[int, str, dict[str, Any]]:
    try:
        validate_document(patch, "patch.schema.json", "patch")
        validate_document(adapter, "adapter.schema.json", "adapter")
        if regions_doc is not None:
            validate_document(regions_doc, "regions.schema.json", "regions")
        if evidence_doc is not None:
            validate_document(evidence_doc, "evidence.schema.json", "evidence")
        require_same_scenario([("patch", patch), ("regions", regions_doc), ("evidence", evidence_doc)])
    except ContractError as exc:
        return 2, str(exc), {}

    allowed = adapter.get("allowedFiles")
    forbidden = adapter.get("forbiddenFiles", [])
    if not isinstance(allowed, list) or not isinstance(forbidden, list):
        return 3, "adapter allowedFiles/forbiddenFiles are required arrays", {}

    checked_files: list[tuple[str, Path]] = []
    for file_name in patch["files"]:
        safe = safe_file(root, file_name)
        if safe is None:
            return 3, f"File escapes root or is invalid: {file_name}", {}
        checked_files.append(safe)
    invalid = [name for name, _ in checked_files if not matches(name, [str(item) for item in allowed])]
    if invalid:
        return 3, "Files outside allowlist: " + ", ".join(invalid), {}
    forbidden_matches = [name for name, _ in checked_files if matches(name, [str(item) for item in forbidden])]
    if forbidden_matches:
        return 3, "Files in forbidden list: " + ", ".join(forbidden_matches), {}

    region: dict[str, Any] | None = None
    if regions_doc is not None:
        region = next((item for item in regions_doc["regions"] if item.get("id") == patch["regionId"]), None)
        if region is None:
            return 3, f"Patch region not found: {patch['regionId']}", {}
        region_evidence = set(region.get("evidenceIds", []))
        if not set(patch["evidenceIds"]).issubset(region_evidence):
            return 3, "Patch evidenceIds are not attached to the target region", {}
        allowed_properties = set(region.get("allowedProperties", []))
        if patch["propertyFamily"] not in allowed_properties:
            return 3, f"propertyFamily is outside region.allowedProperties: {patch['propertyFamily']}", {}
        forbidden_changes = set(region.get("forbiddenChanges", []))
        if patch["propertyFamily"] in forbidden_changes:
            return 3, f"propertyFamily is blocked by region.forbiddenChanges: {patch['propertyFamily']}", {}
        patch_owner = patch.get("visualOwner")
        region_owner = region.get("visualOwner")
        if patch_owner and region_owner not in {"unknown", patch_owner}:
            return 3, f"visualOwner mismatch: patch={patch_owner}, region={region_owner}", {}

    if evidence_doc is not None:
        evidence_ids = {item.get("id") for item in evidence_doc["evidence"] if isinstance(item.get("id"), str)}
        missing_evidence = [item for item in patch["evidenceIds"] if item not in evidence_ids]
        if missing_evidence:
            return 3, "Evidence not found: " + ", ".join(missing_evidence), {}

    hash_map: dict[str, str] = {}
    if isinstance(patch["baseFileSha256"], str):
        if len(checked_files) != 1:
            return 2, "Multiple files require baseFileSha256 as an object", {}
        hash_map[checked_files[0][0]] = patch["baseFileSha256"]
    else:
        for name in patch["files"]:
            hash_map[name] = patch["baseFileSha256"][name]

    if check_hash:
        for name, file_path in checked_files:
            if not file_path.is_file():
                return 3, f"Patch file not found: {name}", {}
            actual = sha256_file(file_path)
            if actual != hash_map[name]:
                return 3, f"baseFileSha256 mismatch for {name}: expected {hash_map[name]}, actual {actual}", {}

    details = {
        "patchId": patch["patchId"],
        "scenarioId": patch["scenarioId"],
        "regionId": patch["regionId"],
        "propertyFamily": patch["propertyFamily"],
        "allowedFiles": [name for name, _ in checked_files],
        "baseFileSha256": hash_map,
        "rollback": patch["rollback"],
        "evidenceIds": patch["evidenceIds"],
        "regionVisualOwner": region.get("visualOwner") if region else None,
    }
    return 0, "patch is valid", details


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a lystar-ui-restore patch.")
    parser.add_argument("--patch", required=True)
    parser.add_argument("--adapter", required=True, help="adapter.json with allowedFiles/forbiddenFiles")
    parser.add_argument("--root", help="Project root used for file hashes", default=".")
    parser.add_argument("--regions", help="regions.json used to verify regionId and evidenceIds")
    parser.add_argument("--evidence", help="evidence.json used to verify evidenceIds")
    parser.add_argument("--check-hash", action="store_true")
    args = parser.parse_args()

    try:
        patch = load(Path(args.patch).expanduser().resolve())
        adapter = load(Path(args.adapter).expanduser().resolve())
        regions_doc = load(Path(args.regions).expanduser().resolve()) if args.regions else None
        evidence_doc = load(Path(args.evidence).expanduser().resolve()) if args.evidence else None
    except ContractError as exc:
        return emit("error", str(exc), 2)

    code, message, details = validate_patch_data(
        patch,
        adapter,
        Path(args.root).expanduser().resolve(),
        args.check_hash,
        regions_doc,
        evidence_doc,
    )
    return emit("valid" if code == 0 else "error" if code == 2 else "blocked", message, code, **details)


if __name__ == "__main__":
    raise SystemExit(main())
