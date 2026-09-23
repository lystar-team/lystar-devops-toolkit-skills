#!/usr/bin/env python3
"""Render, probe, preflight, and compare configured UI restore viewports."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from restore_core import ContractError, atomic_write_json, load_json, require_same_scenario, resolve_workflow_mode

SCRIPT_DIR = Path(__file__).resolve().parent
RENDER_SCRIPT = SCRIPT_DIR / "render_page.py"
GEOMETRY_SCRIPT = SCRIPT_DIR / "geometry_probe.py"
NORMALIZE_SCRIPT = SCRIPT_DIR / "normalize_geometry.py"
PREFLIGHT_SCRIPT = SCRIPT_DIR / "preflight_visual.py"
COMPARE_SCRIPT = SCRIPT_DIR / "compare_visual.py"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, capture_output=True)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def last_json(text: str) -> Any:
    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value
    return None


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.width, image.height
    except (ImportError, FileNotFoundError, OSError):
        return None


def resolve_path(raw: Any, root: Path, config_path: Path) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    root_path = (root / path).resolve()
    if root_path.exists():
        return root_path
    return (config_path.parent / path).resolve()


def profile_viewports(config: dict[str, Any], only: set[str]) -> list[dict[str, Any]]:
    configured = config.get("viewports")
    if configured is None:
        configured = [{"id": "default", **(config.get("viewport") or {})}]
    if not isinstance(configured, list) or not configured:
        raise ContractError("config.viewport or config.viewports must contain a viewport")
    result = []
    for index, item in enumerate(configured, start=1):
        if not isinstance(item, dict):
            raise ContractError(f"config.viewports[{index}] must be an object")
        profile = dict(item)
        profile_id = str(profile.get("id") or f"viewport-{index:02d}")
        if only and profile_id not in only:
            continue
        if not all(key in profile for key in ("width", "height", "deviceScaleFactor")):
            raise ContractError(f"viewport {profile_id} must include width, height and deviceScaleFactor")
        profile["id"] = profile_id
        result.append(profile)
    if only and not result:
        raise ContractError("--only did not match any configured viewport")
    return result


def write_logs(profile_dir: Path, stage: str, completed: subprocess.CompletedProcess[str]) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / f"{stage}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (profile_dir / f"{stage}.stderr.txt").write_text(completed.stderr, encoding="utf-8")


def create_session(adapter: dict[str, Any]) -> tuple[str | None, str | None]:
    scope = str(adapter.get("sessionScope", "worktree"))
    prefix = str(adapter.get("sessionPrefix", "lystar-ui-restore")) + "-matrix"
    completed = run(["agent-browser", "session", "id", "--scope", scope, "--prefix", prefix])
    if completed.returncode != 0 or not completed.stdout.strip():
        return None, completed.stderr.strip() or "unable to create agent-browser session"
    return completed.stdout.strip().splitlines()[-1].strip(), None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run UI restore capture and comparison across viewports.")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--reference", help="Global reference image; a viewport may override it")
    parser.add_argument("--regions", required=True)
    parser.add_argument("--root", required=True, help="Project root")
    parser.add_argument("--out", required=True, help="Matrix output directory")
    parser.add_argument("--assets", help="assets.json passed to preflight_visual.py")
    parser.add_argument("--url", help="Override page URL")
    parser.add_argument("--only", help="Comma-separated viewport ids")
    parser.add_argument("--mode", choices=["iteration", "acceptance"], help="Override config.workflowMode")
    parser.add_argument("--with-geometry", action="store_true", help="Run Geometry in iteration mode")
    parser.add_argument("--with-preflight", action="store_true", help="Run Preflight in iteration mode")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    adapter_path = Path(args.adapter).expanduser().resolve()
    fixture_path = Path(args.fixture).expanduser().resolve()
    default_regions_path = Path(args.regions).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    try:
        config = load_json(config_path, "config.schema.json")
        adapter = load_json(adapter_path, "adapter.schema.json")
        fixture = load_json(fixture_path, "fixture.schema.json")
        default_regions = load_json(default_regions_path, "regions.schema.json")
        scenario = require_same_scenario([("config", config), ("fixture", fixture), ("regions", default_regions)]) or str(config.get("scenarioId", "scenario"))
        workflow_mode = resolve_workflow_mode(config, args.mode)
        profiles = profile_viewports(config, {item.strip() for item in (args.only or "").split(",") if item.strip()})
    except ContractError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    asset_policy = config.get("assetPolicy", {}) if isinstance(config.get("assetPolicy"), dict) else {}
    run_preflight = workflow_mode == "acceptance" or args.with_preflight
    run_geometry = workflow_mode == "acceptance" or args.with_geometry
    if run_preflight and asset_policy.get("requireManifest", False) and not args.assets:
        print(json.dumps({"status": "blocked", "message": "config.assetPolicy.requireManifest requires --assets"}, ensure_ascii=False))
        return 3

    session, session_error = create_session(adapter)
    if not session:
        print(json.dumps({"status": "blocked", "message": session_error}, ensure_ascii=False))
        return 3

    global_reference = resolve_path(args.reference, root, config_path)
    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="lystar-ui-restore-matrix-") as temp_dir:
            temp_root = Path(temp_dir)
            for profile in profiles:
                profile_id = profile["id"]
                profile_dir = out / profile_id
                render_dir = profile_dir / "render"
                geometry_path = profile_dir / "geometry.json"
                structure_map_path = profile_dir / "structure-map.json"
                preflight_dir = profile_dir / "preflight"
                diff_dir = profile_dir / "diff"
                profile_config = dict(config)
                profile_config["workflowMode"] = workflow_mode
                metadata_keys = {"id", "reference", "referencePath", "regions", "regionsPath", "required", "purpose"}
                profile_config["viewport"] = {key: value for key, value in profile.items() if key not in metadata_keys}
                profile_config.pop("viewports", None)
                profile_config_path = temp_root / f"{profile_id}.config.json"
                atomic_write_json(profile_config_path, profile_config, "config.schema.json")

                regions_path = resolve_path(profile.get("regions") or profile.get("regionsPath"), root, config_path) or default_regions_path
                record: dict[str, Any] = {
                    "id": profile_id,
                    "workflowMode": workflow_mode,
                    "viewport": profile_config["viewport"],
                    "status": "blocked",
                    "reference": None,
                    "regions": str(regions_path),
                    "render": None,
                    "geometry": None,
                    "structureMap": None,
                    "preflight": None,
                    "diff": None,
                }

                render_command = [
                    sys.executable, str(RENDER_SCRIPT),
                    "--adapter", str(adapter_path),
                    "--config", str(profile_config_path),
                    "--fixture", str(fixture_path),
                    "--out", str(render_dir),
                    "--session", session,
                    "--keep-session",
                ]
                if args.url:
                    render_command.extend(["--url", args.url])
                rendered = run(render_command)
                write_logs(profile_dir, "render", rendered)
                record["render"] = last_json(rendered.stdout)
                if rendered.returncode != 0:
                    record["reason"] = "render failed"
                    results.append(record)
                    continue

                reference = resolve_path(profile.get("reference") or profile.get("referencePath"), root, config_path) or global_reference
                render_path = render_dir / "render.png"
                reference_size = image_size(reference) if reference else None
                render_size = image_size(render_path)
                reference_matches = bool(reference and reference.exists() and reference_size and render_size and reference_size == render_size)
                if reference is None:
                    reference_reason = "no reference supplied"
                elif not reference.exists():
                    reference_reason = "reference file not found"
                elif reference_size != render_size:
                    reference_reason = f"reference size {list(reference_size or [])} differs from render size {list(render_size or [])}"
                else:
                    reference_reason = "matched"
                record["reference"] = {
                    "path": str(reference) if reference else None,
                    "size": list(reference_size) if reference_size else None,
                    "renderSize": list(render_size) if render_size else None,
                    "matched": reference_matches,
                    "reason": reference_reason,
                }

                structure_map_ready = False
                render_mode = str(profile_config.get("renderMode", "dom-css"))
                if render_mode in {"dom-css", "dom-css-svg", "hybrid", "auto-runtime"} and run_geometry:
                    geometry_command = [
                        sys.executable, str(GEOMETRY_SCRIPT),
                        "--adapter", str(adapter_path),
                        "--config", str(profile_config_path),
                        "--fixture", str(fixture_path),
                        "--regions", str(regions_path),
                        "--capture", str(render_dir / "capture.json"),
                        "--out", str(geometry_path),
                        "--session", session,
                    ]
                    if args.url:
                        geometry_command.extend(["--url", args.url])
                    geometry = run(geometry_command)
                    write_logs(profile_dir, "geometry", geometry)
                    record["geometry"] = last_json(geometry.stdout)
                    if geometry.returncode == 0:
                        normalize_command = [
                            sys.executable, str(NORMALIZE_SCRIPT),
                            "--geometry", str(geometry_path),
                            "--capture", str(render_dir / "capture.json"),
                            "--config", str(profile_config_path),
                            "--regions", str(regions_path),
                            "--out", str(structure_map_path),
                            "--viewport-id", profile_id,
                        ]
                        normalized = run(normalize_command)
                        write_logs(profile_dir, "normalize", normalized)
                        record["structureMap"] = last_json(normalized.stdout)
                        structure_map_ready = normalized.returncode == 0 and structure_map_path.is_file()

                preflight_command = [
                    sys.executable, str(PREFLIGHT_SCRIPT),
                    "--root", str(root),
                    "--render", str(render_path),
                    "--capture", str(render_dir / "capture.json"),
                    "--regions", str(regions_path),
                    "--config", str(profile_config_path),
                    "--mode", workflow_mode,
                    "--out", str(preflight_dir),
                ]
                if reference_matches and reference:
                    preflight_command.extend(["--reference", str(reference)])
                reference_meta_candidates = [
                    config_path.parent / "inspection" / "reference-meta.json",
                    (reference.parent / "reference-meta.json") if reference else None,
                ]
                reference_meta = next((candidate for candidate in reference_meta_candidates if candidate and candidate.is_file()), None)
                if reference_meta:
                    preflight_command.extend(["--reference-meta", str(reference_meta)])
                if structure_map_ready:
                    preflight_command.extend(["--structure-map", str(structure_map_path)])
                evidence_path = config_path.parent / "evidence.json"
                if evidence_path.is_file():
                    preflight_command.extend(["--evidence", str(evidence_path)])
                if args.assets:
                    preflight_command.extend(["--assets", str(Path(args.assets).expanduser().resolve())])
                if run_preflight:
                    preflighted = run(preflight_command)
                    write_logs(profile_dir, "preflight", preflighted)
                    record["preflight"] = last_json(preflighted.stdout)
                    if preflighted.returncode != 0:
                        record["reason"] = "preflight failed"
                        results.append(record)
                        continue
                else:
                    record["preflight"] = {"status": "not-run", "reason": "iteration mode"}

                if not reference_matches:
                    record["diff"] = {"status": "not-run", "reason": reference_reason}
                    record["acceptanceStatus"] = "not-run"
                    record["status"] = "blocked" if bool(profile.get("required", False)) else "unresolved"
                    record["reason"] = "reference comparison not run"
                    results.append(record)
                    continue

                compare_command = [
                    sys.executable, str(COMPARE_SCRIPT),
                    "--reference", str(reference),
                    "--render", str(render_path),
                    "--regions", str(regions_path),
                    "--config", str(profile_config_path),
                    "--capture", str(render_dir / "capture.json"),
                    "--mode", workflow_mode,
                    "--out", str(diff_dir),
                ]
                if structure_map_ready:
                    compare_command.extend(["--structure-map", str(structure_map_path)])
                if run_preflight:
                    compare_command.extend(["--preflight", str(preflight_dir / "preflight.json")])
                compared = run(compare_command)
                write_logs(profile_dir, "compare", compared)
                record["diff"] = last_json(compared.stdout)
                diff_status = record["diff"].get("status") if isinstance(record["diff"], dict) else None
                record["acceptanceStatus"] = diff_status or "blocked"
                if workflow_mode == "iteration":
                    record["status"] = "pass" if compared.returncode == 0 else "blocked"
                else:
                    record["status"] = diff_status or ("accepted" if compared.returncode == 0 else "blocked")
                if compared.returncode != 0:
                    record["status"] = "blocked"
                    record["reason"] = "compare failed"
                results.append(record)
    finally:
        run(["agent-browser", "--session", session, "close"])

    blocked = [item for item in results if item.get("status") == "blocked"]
    unresolved = [item for item in results if item.get("status") in {"unresolved", "not-run"}]
    if workflow_mode == "acceptance":
        acceptance_status = "blocked" if blocked else "unresolved" if unresolved else "accepted"
    else:
        acceptance_status = "not-run"
    summary = {
        "schemaVersion": "1.0",
        "scenarioId": scenario,
        "workflowMode": workflow_mode,
        "status": "blocked" if blocked else "unresolved" if unresolved else "pass",
        "acceptanceStatus": acceptance_status,
        "profiles": results,
        "blockedViewportIds": [item["id"] for item in blocked],
        "unresolvedViewportIds": [item["id"] for item in unresolved],
    }
    summary_path = out / "matrix.json"
    try:
        atomic_write_json(summary_path, summary, "matrix.schema.json")
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": summary["status"], "report": str(summary_path), "profiles": len(results), "blocked": len(blocked), "unresolved": len(unresolved)}, ensure_ascii=False))
    return 3 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
