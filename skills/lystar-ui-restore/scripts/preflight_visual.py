#!/usr/bin/env python3
"""Run schema, asset, capture, and screenshot preflight checks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from restore_core import ContractError, atomic_write_json, close_number, load_json, require_same_scenario, resolve_workflow_mode, sha256_file, stable_hash, urls_match


def emit(status: str, message: str, code: int, **extra: Any) -> int:
    print(json.dumps({"status": status, "message": message, **extra}, ensure_ascii=False))
    return code


def resolve_path(root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def aspect_ratio(width: float, height: float) -> float | None:
    return width / height if width > 0 and height > 0 else None


def close_enough(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance


def probe_image(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required by preflight_visual.py") from exc
    try:
        with Image.open(path) as image:
            result: dict[str, Any] = {
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "aspectRatio": aspect_ratio(image.width, image.height),
                "hasAlpha": "A" in image.getbands(),
            }
            if "A" in image.getbands():
                alpha = image.getchannel("A")
                histogram = alpha.histogram()
                pixel_count = image.width * image.height
                nonzero = sum(histogram[1:])
                result["alphaNonzeroRatio"] = nonzero / pixel_count if pixel_count else 0
                result["alphaBoundingBox"] = list(alpha.getbbox()) if alpha.getbbox() else None
            else:
                result["alphaNonzeroRatio"] = None
                result["alphaBoundingBox"] = None
            sample = image.convert("RGB").resize((32, 32))
            colors = list(sample.get_flattened_data())
            if colors:
                mean = tuple(sum(color[index] for color in colors) / len(colors) for index in range(3))
                variance = sum(sum((color[index] - mean[index]) ** 2 for index in range(3)) for color in colors) / len(colors)
                result["sampleMeanRgb"] = [round(value, 3) for value in mean]
                result["sampleRgbVariance"] = round(variance, 3)
            return result
    except Exception as exc:
        raise RuntimeError(f"Unable to read image {path}: {exc}") from exc


def asset_checks(
    root: Path,
    assets_doc: dict[str, Any],
    require_records: bool,
    require_reviewed: bool,
    require_owner: bool = False,
    require_runtime_evidence: bool = False,
    structure_doc: dict[str, Any] | None = None,
    evidence_doc: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    assets = assets_doc.get("assets", [])
    if not isinstance(assets, list):
        return [], ["assets.json field 'assets' must be an array"], []
    if require_records and not assets:
        return [], ["assets.json has no asset records"], []

    records: list[dict[str, Any]] = []
    blocked: list[str] = []
    warnings: list[str] = []
    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            blocked.append(f"asset[{index}] is not an object")
            continue
        asset_id = str(asset.get("assetId") or f"asset-{index}")
        record: dict[str, Any] = {"assetId": asset_id, "status": "pass", "issues": [], "warnings": []}
        raw_path = asset.get("sourcePath") or asset.get("generatedPath") or asset.get("placeholderPath")
        asset_type = asset.get("type")
        runtime_asset = asset.get("sourceKind") == "runtime" or asset_type in {"canvas", "webgl", "lottie", "rive", "native-runtime"}
        path = resolve_path(root, raw_path)
        if path is None and not runtime_asset:
            record["issues"].append("missing sourcePath/generatedPath/placeholderPath")
        elif path is not None and not path.is_file():
            record["issues"].append(f"asset file not found: {raw_path}")
        elif path is None and runtime_asset:
            record["runtime"] = {"sourceKind": asset.get("sourceKind"), "type": asset_type, "evidenceRequired": True}
            if require_runtime_evidence:
                evidence_ids = asset.get("evidenceIds") if isinstance(asset.get("evidenceIds"), list) else []
                if not evidence_ids:
                    record["issues"].append("runtime asset requires evidenceIds")
                elif evidence_doc is None:
                    record["issues"].append("runtime asset requires evidence.json for evidence binding")
                else:
                    known_evidence = {item.get("id") for item in evidence_doc.get("evidence", []) if isinstance(item, dict)}
                    missing_evidence = sorted(str(item) for item in evidence_ids if item not in known_evidence)
                    if missing_evidence:
                        record["issues"].append(f"runtime evidence IDs are missing: {', '.join(missing_evidence)}")
        if path is not None and path.is_file():
            record["path"] = str(path)
            suffix = path.suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
                try:
                    actual = probe_image(path)
                    record["image"] = actual
                    natural = asset.get("naturalSize")
                    if isinstance(natural, dict):
                        expected = [natural.get("width"), natural.get("height")]
                        if expected != [actual["width"], actual["height"]]:
                            record["issues"].append(f"naturalSize {expected} does not match file size {[actual['width'], actual['height']]}")
                    background = asset.get("background")
                    if isinstance(background, dict):
                        if background.get("mode") == "transparent" and not actual.get("hasAlpha"):
                            record["issues"].append("transparent background is declared but the image has no alpha channel")
                        if background.get("edgeChecked") is not True:
                            record["warnings"].append("background.edgeChecked is not true")
                        if background.get("unintendedFrame") is True:
                            record["issues"].append("background has an unintended frame")
                    geometry = asset.get("geometry")
                    if isinstance(geometry, dict):
                        if geometry.get("aspectChecked") is not True:
                            record["warnings"].append("geometry.aspectChecked is not true")
                        intrinsic = geometry.get("intrinsicAspectRatio")
                        if intrinsic is not None and not close_enough(float(intrinsic), float(actual["aspectRatio"]), float(geometry.get("aspectTolerance") or 0.02)):
                            record["issues"].append("intrinsicAspectRatio does not match the file")
                        display_size = asset.get("displaySize")
                        display_ratio = geometry.get("displayAspectRatio")
                        if isinstance(display_size, dict) and display_ratio is not None:
                            measured_display = aspect_ratio(float(display_size.get("width", 0)), float(display_size.get("height", 0)))
                            if not close_enough(float(display_ratio), measured_display, float(geometry.get("aspectTolerance") or 0.02)):
                                record["issues"].append("displayAspectRatio does not match displaySize")
                except RuntimeError as exc:
                    record["issues"].append(str(exc))
            elif suffix == ".svg":
                text = path.read_text(encoding="utf-8", errors="replace")
                if "<svg" not in text:
                    record["issues"].append("SVG file has no svg root")
                if "viewBox" not in text and "width=" not in text:
                    record["warnings"].append("SVG has no visible viewBox or width attribute")

        if structure_doc and isinstance(asset.get("regionId"), str) and asset.get("regionId") not in {"", "unassigned"}:
            mapping = next((item for item in structure_doc.get("regions", []) if isinstance(item, dict) and item.get("regionId") == asset.get("regionId")), None)
            render_bbox = mapping.get("renderBbox") if isinstance(mapping, dict) else None
            asset_geometry = asset.get("geometry") if isinstance(asset.get("geometry"), dict) else {}
            declared_ratio = asset_geometry.get("displayAspectRatio")
            if isinstance(render_bbox, dict) and float(render_bbox.get("width", 0)) > 0 and float(render_bbox.get("height", 0)) > 0 and declared_ratio is not None:
                measured_ratio = aspect_ratio(float(render_bbox["width"]), float(render_bbox["height"]))
                tolerance = float(asset_geometry.get("aspectTolerance") or 0.02)
                if not close_enough(float(declared_ratio), measured_ratio, tolerance):
                    record["issues"].append("displayAspectRatio does not match normalized geometry")
            elif declared_ratio is not None and mapping is None:
                record["warnings"].append("asset regionId has no normalized geometry mapping")

        if not isinstance(asset.get("visualOwner"), str) or not asset.get("visualOwner"):
            if require_owner:
                record["issues"].append("visualOwner is required")
            else:
                record["warnings"].append("visualOwner is not recorded")
        elif require_owner and asset.get("visualOwner") == "unknown":
            record["issues"].append("visualOwner cannot be unknown")
        if require_reviewed and asset.get("reviewed") is not True:
            record["issues"].append("asset review is required")
        if record["issues"]:
            record["status"] = "blocked"
            blocked.extend(f"{asset_id}: {issue}" for issue in record["issues"])
        if record["warnings"]:
            warnings.extend(f"{asset_id}: {warning}" for warning in record["warnings"])
        records.append(record)
    return records, blocked, warnings


def capture_checks(root: Path, capture: dict[str, Any], config: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    blocked: list[str] = []
    warnings: list[str] = []
    readiness_policy = config.get("readiness", {}) if isinstance(config.get("readiness"), dict) else {}
    readiness = capture.get("readiness", {})
    if not isinstance(readiness, dict):
        blocked.append("capture.readiness must be an object")
        readiness = {}
    if readiness.get("status") != "ready":
        blocked.append(f"capture readiness is {readiness.get('status', 'unknown')}")
    if readiness.get("loginDetected") is True:
        blocked.append("capture detected a login page")
    if readiness.get("blankDetected") is True:
        blocked.append("capture detected a blank page")
    if readiness_policy.get("requireFonts", True) and readiness.get("fonts") != "loaded":
        blocked.append("capture fonts are not loaded")
    if readiness_policy.get("requireImages", True) and readiness.get("imagesComplete") is not True:
        blocked.append("capture images are incomplete")
    if readiness.get("url") and capture.get("route") and not urls_match(readiness.get("url"), capture.get("route")):
        blocked.append("capture readiness URL does not match capture route")
    runtime = capture.get("renderEnvironment", {}).get("runtimeViewport") if isinstance(capture.get("renderEnvironment"), dict) else None
    expected_viewport = capture.get("viewport", {})
    if isinstance(runtime, dict):
        if not close_number(runtime.get("innerWidth"), expected_viewport.get("width")) or not close_number(runtime.get("innerHeight"), expected_viewport.get("height")):
            blocked.append("capture runtime viewport does not match configured viewport")
        if not close_number(runtime.get("devicePixelRatio"), expected_viewport.get("deviceScaleFactor"), 0.01):
            blocked.append("capture runtime DPR does not match configured DPR")

    diagnostics = capture.get("diagnostics") if isinstance(capture.get("diagnostics"), dict) else {}
    errors_path = resolve_path(root, diagnostics.get("errors.txt"))
    error_lines: list[str] = []
    if errors_path and errors_path.is_file():
        for line in errors_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped == "✗" or stripped.startswith("[agent-browser] restore:"):
                continue
            error_lines.append(stripped)
    if readiness_policy.get("failOnPageErrors", True):
        if not errors_path or not errors_path.is_file():
            blocked.append("capture diagnostics.errors.txt is missing")
        elif error_lines:
            blocked.append(f"browser page errors are present: {errors_path}")

    screenshot = resolve_path(root, readiness.get("screenshot"))
    if screenshot is None or not screenshot.is_file():
        blocked.append("capture readiness.screenshot is missing")
    else:
        actual = readiness.get("imageSize")
        configured = config.get("viewport", {})
        expected = [configured.get("imageWidth"), configured.get("imageHeight")]
        if expected[0] is not None and expected[1] is not None and actual != expected:
            blocked.append(f"capture imageSize {actual} does not match configured image size {expected}")
        stats = readiness.get("imageStats")
        suspicious = isinstance(stats, dict) and (float(stats.get("whiteRatio", 0)) >= 0.995 or float(stats.get("blackRatio", 0)) >= 0.995)
        if suspicious:
            message = "capture screenshot has suspicious uniform luminance"
            if readiness_policy.get("allowSuspiciousScreenshot", False):
                warnings.append(message)
            else:
                blocked.append(message)
    return blocked, warnings, {"readiness": readiness, "screenshot": str(screenshot) if screenshot else None, "pageErrors": error_lines}


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight UI restore assets, capture readiness and screenshot inputs.")
    parser.add_argument("--root", required=True, help="Project root")
    parser.add_argument("--reference", help="Reference image")
    parser.add_argument("--render", help="Render image")
    parser.add_argument("--reference-meta", help="inspection/reference-meta.json")
    parser.add_argument("--capture", help="capture.json")
    parser.add_argument("--regions", help="regions.json")
    parser.add_argument("--evidence", help="evidence.json for runtime asset evidence binding")
    parser.add_argument("--assets", help="assets.json")
    parser.add_argument("--config", help="config.json")
    parser.add_argument("--structure-map", help="Normalized structure-map.json for display ratio checks")
    parser.add_argument("--mode", choices=["iteration", "acceptance"], help="Override config.workflowMode")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--require-assets", action="store_true", help="Require at least one asset record")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    blocked: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}

    reference_path = resolve_path(root, args.reference)
    render_path = resolve_path(root, args.render)
    for key, path, raw in (("reference", reference_path, args.reference), ("render", render_path, args.render)):
        if path:
            if not path.is_file():
                blocked.append(f"{key} file not found: {raw}")
            else:
                try:
                    details[key] = probe_image(path)
                    details[key]["sha256"] = sha256_file(path)
                except RuntimeError as exc:
                    blocked.append(str(exc))
    if reference_path and render_path and reference_path.is_file() and render_path.is_file() and "reference" in details and "render" in details:
        reference_size = [details["reference"]["width"], details["reference"]["height"]]
        render_size = [details["render"]["width"], details["render"]["height"]]
        details["sameImageSize"] = reference_size == render_size
        if reference_size != render_size:
            blocked.append(f"reference size {reference_size} differs from render size {render_size}")

    config: dict[str, Any] = {}
    capture: dict[str, Any] | None = None
    regions_doc: dict[str, Any] | None = None
    assets: dict[str, Any] | None = None
    evidence_doc: dict[str, Any] | None = None
    reference_meta: dict[str, Any] | None = None
    structure_map: dict[str, Any] | None = None
    workflow_mode = "iteration"
    try:
        if args.config:
            config_path = resolve_path(root, args.config)
            if config_path is None or not config_path.is_file():
                blocked.append(f"config file not found: {args.config}")
            else:
                config = load_json(config_path, "config.schema.json")
        if args.reference_meta:
            reference_meta_path = resolve_path(root, args.reference_meta)
            if reference_meta_path is None or not reference_meta_path.is_file():
                blocked.append(f"reference meta file not found: {args.reference_meta}")
            else:
                reference_meta = load_json(reference_meta_path, "reference-meta.schema.json")
        if args.capture:
            capture_path = resolve_path(root, args.capture)
            if capture_path is None or not capture_path.is_file():
                blocked.append(f"capture file not found: {args.capture}")
            else:
                capture = load_json(capture_path, "capture.schema.json")
        if args.regions:
            regions_path = resolve_path(root, args.regions)
            if regions_path is None or not regions_path.is_file():
                blocked.append(f"regions file not found: {args.regions}")
            else:
                regions_doc = load_json(regions_path, "regions.schema.json")
        if args.evidence:
            evidence_path = resolve_path(root, args.evidence)
            if evidence_path is None or not evidence_path.is_file():
                blocked.append(f"evidence file not found: {args.evidence}")
            else:
                evidence_doc = load_json(evidence_path, "evidence.schema.json")
        if args.assets:
            assets_path = resolve_path(root, args.assets)
            if assets_path is None or not assets_path.is_file():
                blocked.append(f"assets file not found: {args.assets}")
            else:
                assets = load_json(assets_path, "assets.schema.json")
        if args.structure_map:
            structure_path = resolve_path(root, args.structure_map)
            if structure_path is None or not structure_path.is_file():
                blocked.append(f"structure map file not found: {args.structure_map}")
            else:
                structure_map = load_json(structure_path, "structure-map.schema.json")
        require_same_scenario([
            ("config", config or None),
            ("referenceMeta", reference_meta),
            ("capture", capture),
            ("regions", regions_doc),
            ("evidence", evidence_doc),
            ("assets", assets),
            ("structureMap", structure_map),
        ])
        workflow_mode = resolve_workflow_mode(config, args.mode)
    except ContractError as exc:
        blocked.append(str(exc))

    asset_policy = config.get("assetPolicy", {}) if isinstance(config.get("assetPolicy"), dict) else {}
    layer_policy = config.get("layerPolicy", {}) if isinstance(config.get("layerPolicy"), dict) else {}
    if workflow_mode == "acceptance" and capture is None:
        blocked.append("acceptance mode requires capture.json")
    if workflow_mode == "acceptance" and reference_meta is None:
        blocked.append("acceptance mode requires reference-meta.json")
    runtime_render_mode = str(config.get("renderMode", "dom-css")) in {"canvas", "webgl", "3d", "native-runtime"}
    if workflow_mode == "acceptance" and runtime_render_mode:
        runtime_assets = [
            asset for asset in (assets or {}).get("assets", [])
            if isinstance(asset, dict) and (asset.get("sourceKind") == "runtime" or asset.get("type") in {"canvas", "webgl", "native-runtime"})
        ]
        if not runtime_assets:
            blocked.append("acceptance runtime render requires at least one runtime asset record")
    if structure_map is not None:
        if capture is not None and structure_map.get("captureHash") != stable_hash(capture):
            blocked.append("structure map captureHash does not match capture")
        if regions_doc is not None and structure_map.get("regionsHash") != stable_hash(regions_doc):
            blocked.append("structure map regionsHash does not match regions")
        if workflow_mode == "acceptance" and structure_map.get("status") != "verified":
            blocked.append("acceptance mode requires a verified structure map")
    if reference_meta is not None:
        meta_reference = resolve_path(root, reference_meta.get("reference"))
        if reference_path and meta_reference and meta_reference != reference_path:
            blocked.append("reference meta points to a different reference image")
        if reference_path and reference_path.is_file() and reference_meta.get("sha256") != sha256_file(reference_path):
            blocked.append("reference image hash does not match reference meta")
        if reference_path and reference_meta.get("width") != details.get("reference", {}).get("width"):
            blocked.append("reference meta width does not match reference image")
        if reference_path and reference_meta.get("height") != details.get("reference", {}).get("height"):
            blocked.append("reference meta height does not match reference image")
        details["referenceMeta"] = {
            "reference": str(meta_reference) if meta_reference else None,
            "sha256": reference_meta.get("sha256"),
            "width": reference_meta.get("width"),
            "height": reference_meta.get("height"),
        }
    if asset_policy.get("requireManifest", False) and assets is None:
        blocked.append("config.assetPolicy.requireManifest requires assets.json")
    if capture is not None:
        if capture.get("workflowMode") not in (None, workflow_mode):
            blocked.append(f"capture workflowMode is {capture.get('workflowMode', 'unknown')}, expected {workflow_mode}")
        capture_blocked, capture_warnings, capture_details = capture_checks(root, capture, config)
        blocked.extend(capture_blocked)
        warnings.extend(capture_warnings)
        details["capture"] = capture_details
        readiness = capture.get("readiness", {})
        captured_hash = readiness.get("imageSha256") if isinstance(readiness, dict) else None
        if captured_hash is None and isinstance(capture.get("sourceHashes"), dict):
            captured_hash = capture["sourceHashes"].get("renderImageSha256")
        if workflow_mode == "acceptance" and not captured_hash:
            blocked.append("acceptance mode requires capture render image hash")
        if captured_hash and render_path and render_path.is_file() and sha256_file(render_path) != captured_hash:
            blocked.append("render image hash does not match capture")
    if assets is not None:
        records, asset_blocked, asset_warnings = asset_checks(
            root,
            assets,
            args.require_assets,
            bool(asset_policy.get("requireReviewedAssets", False)),
            bool(layer_policy.get("requireVisualOwner", False)),
            workflow_mode == "acceptance",
            structure_map,
            evidence_doc,
        )
        details["assets"] = records
        blocked.extend(asset_blocked)
        warnings.extend(asset_warnings)

    status = "blocked" if blocked else "warn" if warnings else "pass"
    report = {
        "schemaVersion": "1.0",
        "scenarioId": str(config.get("scenarioId") or (capture or {}).get("scenarioId") or (reference_meta or {}).get("scenarioId") or "scenario"),
        "workflowMode": workflow_mode,
        "status": status,
        "root": str(root),
        "reference": str(reference_path) if reference_path else None,
        "render": str(render_path) if render_path else None,
        "blocked": blocked,
        "warnings": warnings,
        "details": details,
    }
    report_path = out / "preflight.json"
    try:
        atomic_write_json(report_path, report, "preflight.schema.json")
    except (ContractError, OSError) as exc:
        return emit("error", str(exc), 2)
    return emit(status, "preflight completed" if not blocked else "preflight blocked", 3 if blocked else 0, report=str(report_path), blocked=blocked, warnings=warnings)


if __name__ == "__main__":
    raise SystemExit(main())
