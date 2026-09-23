#!/usr/bin/env python3
"""Unified command entrypoint for the lystar-ui-restore toolchain."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COMMANDS = {
    "init": "init_scenario.py",
    "inspect": "inspect_reference.py",
    "catalog": "asset_catalog.py",
    "capture": "render_page.py",
    "geometry": "geometry_probe.py",
    "normalize": "normalize_geometry.py",
    "preflight": "preflight_visual.py",
    "compare": "compare_visual.py",
    "focus": "focus_diff.py",
    "matrix": "run_matrix.py",
    "validate-patch": "validate_patch.py",
    "prepare-patch": "prepare_scoped_patch.py",
    "verify-patch": "verify_applied_patch.py",
    "validate-state": "validate_state.py",
    "record": "record_restore_state.py",
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        names = "\n  ".join(sorted(COMMANDS))
        print(f"Usage: python3 ui_restore.py <command> [args]\n\nCommands:\n  {names}")
        return 0 if len(sys.argv) >= 2 else 2
    command = sys.argv[1]
    script = COMMANDS.get(command)
    if script is None:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    completed = subprocess.run([sys.executable, str(SCRIPT_DIR / script), *sys.argv[2:]])
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
