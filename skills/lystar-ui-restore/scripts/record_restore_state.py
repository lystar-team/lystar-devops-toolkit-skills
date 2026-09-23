#!/usr/bin/env python3
"""Persist scenario-scoped lystar-ui-restore documents, state, report, and patch history."""
from __future__ import annotations

import argparse
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from restore_core import (
    ContractError,
    atomic_write_json,
    load_json,
    require_same_scenario,
    stable_hash,
    validate_document,
    sha256_file,
)

DOCUMENTS = {
    "evidence": ("evidence.json", "evidence.schema.json"),
    "regions": ("regions.json", "regions.schema.json"),
    "tokens": ("tokens.json", "tokens.schema.json"),
    "content": ("content.json", "content.schema.json"),
    "assets": ("assets.json", "assets.schema.json"),
    "renderResult": ("render-result.json", None),
    "diffReport": ("diff-report.json", "diff.schema.json"),
}


def fail(message: str, code: int = 2) -> None:
    print(json.dumps({"status": "error" if code == 2 else "blocked", "message": message}, ensure_ascii=False))
    raise SystemExit(code)


def output_path(root: Path, raw_path: str) -> Path:
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        fail(f"Output path escapes root: {raw_path}", 3)
    return candidate


def existing_patch_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            previous = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(previous, dict) and isinstance(previous.get("patchId"), str):
            ids.add(previous["patchId"])
    return ids


def append_patch(path: Path, patch: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        ids = set()
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("patchId"), str):
                ids.add(value["patchId"])
        if patch["patchId"] in ids:
            raise ContractError(f"duplicate patchId: {patch['patchId']}")
        handle.seek(0, 2)
        handle.write(json.dumps(patch, ensure_ascii=False) + "\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def embedded_document(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def acceptance_diff(payload: dict[str, Any], state: dict[str, Any] | None, report: dict[str, Any], root: Path, scenario_root: Path) -> dict[str, Any]:
    if report.get("visualStatus") != "accepted":
        raise ContractError("accepted report requires report.visualStatus=accepted")
    if report.get("unverifiedViewports"):
        raise ContractError("accepted report cannot contain unverifiedViewports")
    diff_ref = payload.get("diffReport")
    if diff_ref is None and state:
        diff_ref = state.get("diffReport")
    if isinstance(diff_ref, dict):
        diff = diff_ref
    elif isinstance(diff_ref, str) and diff_ref:
        candidate = Path(diff_ref).expanduser()
        if not candidate.is_absolute():
            candidate = scenario_root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise ContractError("accepted report diffReport escapes the visual-restore root") from exc
        if not candidate.is_file():
            raise ContractError(f"accepted report diffReport is missing: {candidate}")
        diff = load_json(candidate, "diff.schema.json")
    else:
        raise ContractError("accepted report requires an embedded or referenced diffReport")
    validate_document(diff, "diff.schema.json", "diffReport")
    require_same_scenario([("report", report), ("diffReport", diff)])
    if diff.get("status") != "accepted":
        raise ContractError("accepted report requires diffReport.status=accepted")
    if diff.get("workflowMode") != "acceptance":
        raise ContractError("accepted report requires an acceptance-mode diffReport")
    gate = diff.get("acceptanceGate") if isinstance(diff.get("acceptanceGate"), dict) else {}
    if gate.get("accepted") is not True:
        raise ContractError("accepted report requires diffReport.acceptanceGate.accepted=true")
    preflight = diff.get("preflight") if isinstance(diff.get("preflight"), dict) else {}
    if preflight.get("status") != "pass":
        raise ContractError("accepted report requires diffReport.preflight.status=pass")
    capture = diff.get("capture") if isinstance(diff.get("capture"), dict) else {}
    if capture.get("provided") is not True:
        raise ContractError("accepted report requires a capture-backed diff")
    pixel = diff.get("pixel") if isinstance(diff.get("pixel"), dict) else {}
    source_hashes = diff.get("sourceHashes") if isinstance(diff.get("sourceHashes"), dict) else {}
    if not isinstance(source_hashes.get("referenceSha256"), str) or not isinstance(source_hashes.get("renderSha256"), str):
        raise ContractError("accepted report requires reference and render image hashes")
    if source_hashes.get("renderSha256") != diff.get("capture", {}).get("readiness", {}).get("imageSha256"):
        raise ContractError("accepted report render hash does not match capture hash")
    for field, hash_key in (("reference", "referenceSha256"), ("render", "renderSha256")):
        raw_path = diff.get(field)
        if not isinstance(raw_path, str) or not raw_path:
            raise ContractError(f"accepted report diffReport.{field} path is required")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = scenario_root / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise ContractError(f"accepted report {field} image is missing: {candidate}")
        if sha256_file(candidate) != source_hashes[hash_key]:
            raise ContractError(f"accepted report {field} image hash does not match diffReport")
    return diff


def main() -> int:
    parser = argparse.ArgumentParser(description="Record lystar-ui-restore evidence, state and report by scenario.")
    parser.add_argument("--root", required=True, help=".visual-restore root")
    parser.add_argument("--input", required=True, help="restore-result.json")
    parser.add_argument("--scenario-id")
    parser.add_argument("--state-path")
    parser.add_argument("--report-path")
    parser.add_argument("--patch-log")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    try:
        payload = load_json(Path(args.input).expanduser().resolve(), "restore-result.schema.json")
    except ContractError as exc:
        fail(str(exc), 2)

    state = embedded_document(payload.get("state"))
    report = embedded_document(payload.get("report"))
    patch = embedded_document(payload.get("patch"))
    embedded_documents = [(key, embedded_document(payload.get(key))) for key in DOCUMENTS]
    try:
        inferred_scenario = require_same_scenario([
            ("state", state),
            ("report", report),
            ("patch", patch),
            *embedded_documents,
        ])
        if args.scenario_id and inferred_scenario and args.scenario_id != inferred_scenario:
            raise ContractError(f"scenarioId mismatch: argument={args.scenario_id}, document={inferred_scenario}")
        scenario = args.scenario_id or inferred_scenario
        if not scenario:
            raise ContractError("scenarioId is required in --scenario-id or an embedded document")
        if state is not None:
            validate_document(state, "state.schema.json", "state")
        if report is not None:
            validate_document(report, "report.schema.json", "report")
        if patch is not None:
            validate_document(patch, "patch.schema.json", "patch")
        for key, document in embedded_documents:
            schema = DOCUMENTS[key][1]
            if document is not None and schema:
                validate_document(document, schema, key)
    except ContractError as exc:
        fail(str(exc), 2)

    scenario_root = root / "scenarios" / scenario
    if report and report.get("status") == "accepted":
        try:
            acceptance_diff(payload, state, report, root, scenario_root)
        except ContractError as exc:
            fail(str(exc), 3)
    state_path = output_path(root, args.state_path) if args.state_path else scenario_root / "state.json"
    report_path = output_path(root, args.report_path) if args.report_path else scenario_root / "report.json"
    patch_log_path = output_path(root, args.patch_log) if args.patch_log else scenario_root / "patches.jsonl"

    artifact_paths: dict[str, str] = {}
    written_hashes: dict[str, str] = {}
    try:
        for key, (default_name, schema) in DOCUMENTS.items():
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                artifact_paths[key] = value
                continue
            path = scenario_root / default_name
            atomic_write_json(path, value, schema)
            artifact_paths[key] = str(path)
            written_hashes[key] = stable_hash(value)
        if state is not None:
            atomic_write_json(state_path, state, "state.schema.json")
            written_hashes["state"] = stable_hash(state)
        if report is not None:
            atomic_write_json(report_path, report, "report.schema.json")
            written_hashes["report"] = stable_hash(report)
        if patch is not None:
            append_patch(patch_log_path, patch)
            written_hashes["patch"] = stable_hash(patch)
    except (ContractError, OSError) as exc:
        fail(f"unable to write restore state: {exc}", 4 if isinstance(exc, OSError) else 3)

    manifest = {
        "schemaVersion": "1.0",
        "scenarioId": scenario,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "state": str(state_path) if state is not None else None,
        "report": str(report_path) if report is not None else None,
        "patchLog": str(patch_log_path) if patch is not None else None,
        "artifacts": artifact_paths,
        "hashes": written_hashes,
    }
    atomic_write_json(scenario_root / "record-manifest.json", manifest)
    print(json.dumps({
        "status": "completed",
        "scenarioId": scenario,
        "scenarioRoot": str(scenario_root),
        "state": str(state_path) if state is not None else None,
        "stateSha256": stable_hash(state) if state is not None else None,
        "report": str(report_path) if report is not None else None,
        "reportStatus": report.get("status") if report else None,
        "nextActiveRegion": payload.get("nextActiveRegion", state.get("activeRegion") if state else None),
        "artifacts": artifact_paths,
        "patch": str(patch_log_path) if patch is not None else None,
        "manifest": str(scenario_root / "record-manifest.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
