#!/usr/bin/env python3
"""Verify declared patch files after Pi edit without mutating source files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from restore_core import ContractError, atomic_write_json, load_json, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify file hashes after a prepared patch is applied.")
    parser.add_argument("--patch", required=True, help="validated patch JSON")
    parser.add_argument("--root", required=True, help="Project root")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    try:
        patch = load_json(Path(args.patch).expanduser().resolve(), "patch.schema.json")
    except ContractError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    root = Path(args.root).expanduser().resolve()
    base = patch["baseFileSha256"]
    base_map = {patch["files"][0]: base} if isinstance(base, str) else base
    records = []
    blocked = []
    for raw in patch["files"]:
        path = (root / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            blocked.append(f"file escapes root: {raw}")
            continue
        if not path.is_file():
            blocked.append(f"file is missing: {raw}")
            continue
        current = sha256_file(path)
        records.append({"file": raw, "baseSha256": base_map[raw], "currentSha256": current, "changed": current != base_map[raw]})
    if records and not any(item["changed"] for item in records):
        blocked.append("declared patch files have no content change")
    result = {
        "schemaVersion": "1.0",
        "patchId": patch["patchId"],
        "scenarioId": patch["scenarioId"],
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "status": "accepted" if not blocked else "blocked",
        "files": records,
        "blocked": blocked,
    }
    try:
        output = Path(args.out).expanduser().resolve()
        atomic_write_json(output, result, "patch-verification.schema.json")
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "verification": str(output), "blocked": blocked}, ensure_ascii=False))
    return 0 if not blocked else 3


if __name__ == "__main__":
    raise SystemExit(main())
