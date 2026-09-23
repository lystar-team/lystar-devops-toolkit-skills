#!/usr/bin/env python3
"""Prepare a reversible, validated patch for Pi edit without mutating source files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_patch import load, validate_patch_data
from restore_core import ContractError, atomic_write_json


def write_json(path: Path, value: Any, schema_name: str | None = None) -> None:
    atomic_write_json(path, value, schema_name)


def emit(status: str, message: str, code: int, **extra: Any) -> int:
    print(json.dumps({"status": status, "message": message, **extra}, ensure_ascii=False))
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and prepare a scoped patch for Pi edit.")
    parser.add_argument("--patch", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--regions", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--out", help="Optional validated patch output path")
    args = parser.parse_args()

    try:
        patch = load(Path(args.patch).expanduser().resolve())
        adapter = load(Path(args.adapter).expanduser().resolve())
        regions = load(Path(args.regions).expanduser().resolve())
        evidence = load(Path(args.evidence).expanduser().resolve())
    except (ContractError, ValueError) as exc:
        return emit("error", str(exc), 2)

    code, message, details = validate_patch_data(
        patch,
        adapter,
        Path(args.root).expanduser().resolve(),
        check_hash=True,
        regions_doc=regions,
        evidence_doc=evidence,
    )
    if code != 0:
        blocked = {"status": "blocked" if code == 3 else "error", "message": message, "patchId": patch.get("patchId"), "rollback": patch.get("rollback")}
        if args.out:
            write_json(Path(args.out).expanduser().resolve(), blocked)
        return emit(blocked["status"], message, code, patchId=patch.get("patchId"), rollback=patch.get("rollback"))

    prepared = dict(patch)
    prepared["validation"] = {
        "status": "valid",
        "validatedAt": datetime.now(timezone.utc).isoformat(),
        "allowedFiles": details["allowedFiles"],
        "baseFileSha256": details["baseFileSha256"],
        "evidenceIds": details["evidenceIds"]
    }
    prepared["sourceMutation"] = "Pi.edit"
    prepared.setdefault("rollback", details["rollback"])
    if args.out:
        write_json(Path(args.out).expanduser().resolve(), prepared, "patch.schema.json")
    return emit(
        "valid",
        "patch prepared; Pi edit is the source mutation step",
        0,
        patchId=prepared["patchId"],
        validatedPatch=str(Path(args.out).expanduser().resolve()) if args.out else None,
        allowedFiles=details["allowedFiles"],
        rollback=details["rollback"],
        sourceMutation="Pi.edit"
    )


if __name__ == "__main__":
    raise SystemExit(main())
