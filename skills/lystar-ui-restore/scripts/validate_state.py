#!/usr/bin/env python3
"""Validate a lystar-ui-restore state file with its JSON Schema and referenced paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from restore_core import ContractError, load_json


def result(status: str, message: str, code: int) -> int:
    print(json.dumps({"status": status, "message": message}, ensure_ascii=False))
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate lystar-ui-restore state.json.")
    parser.add_argument("--state", required=True)
    parser.add_argument("--root", help="Root used to resolve referenced files")
    parser.add_argument("--require-files", action="store_true", help="Require all referenced files to exist")
    args = parser.parse_args()
    path = Path(args.state).expanduser().resolve()
    try:
        state = load_json(path, "state.schema.json")
    except ContractError as exc:
        return result("error", str(exc), 2)

    if args.require_files:
        root = Path(args.root).expanduser().resolve() if args.root else path.parent
        references = [state.get(key) for key in ("reference", "capture", "regions", "tokens", "content", "assets", "decisions", "currentRender", "diffReport")]
        missing_paths = []
        for reference in references:
            if not reference:
                continue
            candidate = (root / str(reference)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return result("blocked", f"Referenced path escapes root: {reference}", 3)
            if not candidate.exists():
                missing_paths.append(str(reference))
        if missing_paths:
            return result("blocked", "Missing referenced files: " + ", ".join(missing_paths), 3)
    return result("valid", "state.json is valid", 0)


if __name__ == "__main__":
    raise SystemExit(main())
