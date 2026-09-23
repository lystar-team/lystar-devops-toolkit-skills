#!/usr/bin/env python3
"""Compare screenshots with vectorized pixel, region, anchor, and structure metrics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from restore_core import ContractError, atomic_write_json, load_json, require_same_scenario, resolve_workflow_mode, sha256_file, stable_hash


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def box(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, list) and len(value) == 4:
        try:
            return tuple(int(round(float(item))) for item in value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        try:
            return (
                int(round(float(value["x"]))),
                int(round(float(value["y"]))),
                int(round(float(value["width"]))),
                int(round(float(value["height"]))),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def clipped(value: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int] | None:
    x, y, region_width, region_height = value
    left, top = max(0, x), max(0, y)
    right = min(width, x + max(0, region_width))
    bottom = min(height, y + max(0, region_height))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def gate_mask_boxes(config: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    result = []
    for item in config.get("masks", []):
        if not isinstance(item, dict) or item.get("participatesInGate") is True:
            continue
        parsed = box(item.get("bbox"))
        if parsed:
            result.append(parsed)
    return result


def integral_image(values):
    import numpy as np

    return np.pad(values.astype(np.int64).cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))


def rectangle_sum(integral, bounds: tuple[int, int, int, int]) -> int:
    left, top, right, bottom = bounds
    return int(integral[bottom, right] - integral[top, right] - integral[bottom, left] + integral[top, left])


def vectorized_diff(reference, render, masks: list[tuple[int, int, int, int]], threshold: float):
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("numpy is required by compare_visual.py") from exc

    width = max(reference.width, render.width)
    height = max(reference.height, render.height)
    ref_array = np.zeros((height, width, 3), dtype=np.uint8)
    render_array = np.zeros((height, width, 3), dtype=np.uint8)
    ref_present = np.zeros((height, width), dtype=bool)
    render_present = np.zeros((height, width), dtype=bool)
    reference_pixels = np.asarray(reference, dtype=np.uint8)
    render_pixels = np.asarray(render, dtype=np.uint8)
    ref_array[: reference.height, : reference.width] = reference_pixels
    render_array[: render.height, : render.width] = render_pixels
    ref_present[: reference.height, : reference.width] = True
    render_present[: render.height, : render.width] = True

    overlap = ref_present & render_present
    delta = ref_array.astype(np.int32) - render_array.astype(np.int32)
    squared_distance = np.sum(delta * delta, axis=2)
    threshold_squared = float(threshold) * float(threshold) * 3.0 * 255.0 * 255.0
    different = (~overlap) | (squared_distance > threshold_squared)

    excluded = np.zeros((height, width), dtype=bool)
    for raw in masks:
        bounds = clipped(raw, width, height)
        if bounds:
            left, top, right, bottom = bounds
            excluded[top:bottom, left:right] = True
    comparable = ~excluded
    different &= comparable

    diff_rgba = np.zeros((height, width, 4), dtype=np.uint8)
    diff_rgba[different] = (255, 0, 0, 220)
    return different, comparable, diff_rgba


def mapping_index(structure_map: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(structure_map, dict):
        return {}
    return {
        str(item["regionId"]): item
        for item in structure_map.get("regions", [])
        if isinstance(item, dict) and isinstance(item.get("regionId"), str)
    }


def metric_for_region(
    region: dict[str, Any],
    reference_size: tuple[int, int],
    diff_integral,
    comparable_integral,
    mapping: dict[str, Any] | None,
    is_leaf: bool,
) -> dict[str, Any]:
    reference_box = clipped(box(region.get("bbox")) or (0, 0, 0, 0), *reference_size)
    render_box = box(mapping.get("renderBbox")) if isinstance(mapping, dict) else box(region.get("renderBbox"))
    render_present = mapping.get("matched") if isinstance(mapping, dict) else region.get("renderPresent")
    mapping_state = mapping.get("state") if isinstance(mapping, dict) else "unavailable"
    if not reference_box:
        return {
            "id": region.get("id"),
            "role": region.get("role"),
            "isLeaf": is_leaf,
            "status": "unavailable",
            "localDiffDensity": None,
            "anchorError": None,
            "matched": False,
            "mappingState": mapping_state,
        }
    left, top, right, bottom = reference_box
    diff_pixels = rectangle_sum(diff_integral, reference_box)
    comparable_pixels = rectangle_sum(comparable_integral, reference_box)
    anchor_error = None
    if render_box:
        render_x, render_y, render_width, render_height = render_box
        anchor_error = max(
            abs(left - render_x),
            abs(top - render_y),
            abs((right - left) - render_width),
            abs((bottom - top) - render_height),
        )
    matched = bool(render_box and render_present is not False)
    return {
        "id": region.get("id"),
        "role": region.get("role"),
        "isLeaf": is_leaf,
        "referenceBbox": {"x": left, "y": top, "width": right - left, "height": bottom - top},
        "renderBbox": {"x": render_box[0], "y": render_box[1], "width": render_box[2], "height": render_box[3]} if render_box else None,
        "diffPixels": diff_pixels,
        "comparablePixels": comparable_pixels,
        "localDiffDensity": diff_pixels / comparable_pixels if comparable_pixels else None,
        "anchorError": anchor_error,
        "matched": matched,
        "renderPresent": render_present,
        "mappingState": mapping_state,
        "requiredForAcceptance": bool(region.get("requiredForAcceptance") or (region.get("visualChecks") or {}).get("requiredForAcceptance")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two UI screenshots and emit diff.png/diff.json.")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--render", required=True)
    parser.add_argument("--regions", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--capture", help="capture.json produced by render_page.py")
    parser.add_argument("--preflight", help="preflight.json produced by preflight_visual.py")
    parser.add_argument("--structure-map", help="Normalized structure-map.json")
    parser.add_argument("--mode", choices=["iteration", "acceptance"], help="Override config.workflowMode")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()

    try:
        from PIL import Image
    except ImportError:
        fail("Pillow is required by compare_visual.py", 3)

    reference_path = Path(args.reference).expanduser().resolve()
    render_path = Path(args.render).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if not reference_path.is_file() or not render_path.is_file():
        fail("Reference or render image is missing", 3)

    try:
        config = load_json(Path(args.config).expanduser().resolve(), "config.schema.json")
        regions_doc = load_json(Path(args.regions).expanduser().resolve(), "regions.schema.json")
        capture = load_json(Path(args.capture).expanduser().resolve(), "capture.schema.json") if args.capture else None
        preflight = load_json(Path(args.preflight).expanduser().resolve(), "preflight.schema.json") if args.preflight else None
        structure_map = load_json(Path(args.structure_map).expanduser().resolve(), "structure-map.schema.json") if args.structure_map else None
        scenario = require_same_scenario([
            ("config", config), ("regions", regions_doc), ("capture", capture), ("structureMap", structure_map), ("preflight", preflight)
        ]) or str(config.get("scenarioId", "scenario"))
        workflow_mode = resolve_workflow_mode(config, args.mode)
    except ContractError as exc:
        fail(str(exc), 2)

    if capture is not None:
        readiness = capture.get("readiness", {})
        if not isinstance(readiness, dict) or readiness.get("status") != "ready":
            fail(f"Capture is not ready for visual comparison: {readiness.get('status', 'unknown') if isinstance(readiness, dict) else 'invalid'}", 3)
        if readiness.get("loginDetected") is True or readiness.get("blankDetected") is True:
            fail("Capture points to a login or blank page", 3)

    input_issues: list[str] = []
    if capture is not None and capture.get("workflowMode") not in (None, workflow_mode):
        input_issues.append(f"capture workflowMode is {capture.get('workflowMode', 'unknown')}, expected {workflow_mode}")
    if structure_map is not None and structure_map.get("workflowMode") not in (None, workflow_mode):
        input_issues.append(f"structure map workflowMode is {structure_map.get('workflowMode', 'unknown')}, expected {workflow_mode}")
    if preflight is not None and preflight.get("workflowMode") not in (None, workflow_mode):
        input_issues.append(f"preflight workflowMode is {preflight.get('workflowMode', 'unknown')}, expected {workflow_mode}")
    structure_required = str(config.get("renderMode", "dom-css")) in {"dom-css", "dom-css-svg", "hybrid", "auto-runtime"}
    if workflow_mode == "acceptance":
        if capture is None:
            input_issues.append("acceptance mode requires capture.json")
        if preflight is None:
            input_issues.append("acceptance mode requires preflight.json")
        if structure_required and structure_map is None:
            input_issues.append("acceptance mode requires structure-map.json")
        if not regions_doc.get("regions"):
            input_issues.append("acceptance mode requires at least one reference region")
        if isinstance(preflight, dict) and preflight.get("status") != "pass":
            input_issues.append(f"preflight status is {preflight.get('status', 'unknown')}")


    if capture is not None:
        readiness = capture.get("readiness", {})
        captured_hash = readiness.get("imageSha256") if isinstance(readiness, dict) else None
        if captured_hash is None and isinstance(capture.get("sourceHashes"), dict):
            captured_hash = capture["sourceHashes"].get("renderImageSha256")
        if workflow_mode == "acceptance" and not captured_hash:
            input_issues.append("capture does not contain a render image hash")
        if captured_hash:
            try:
                actual_hash = sha256_file(render_path)
                if actual_hash != captured_hash:
                    input_issues.append("render image hash does not match capture")
            except OSError as exc:
                input_issues.append(f"unable to hash render image: {exc}")

    if preflight is not None:
        preflight_details = preflight.get("details") if isinstance(preflight.get("details"), dict) else {}
        preflight_reference_hash = (preflight_details.get("reference") or {}).get("sha256") if isinstance(preflight_details.get("reference"), dict) else None
        preflight_render_hash = (preflight_details.get("render") or {}).get("sha256") if isinstance(preflight_details.get("render"), dict) else None
        if workflow_mode == "acceptance" and not preflight_reference_hash:
            input_issues.append("preflight does not contain a reference image hash")
        if workflow_mode == "acceptance" and not preflight_render_hash:
            input_issues.append("preflight does not contain a render image hash")
        if preflight_reference_hash and preflight_reference_hash != sha256_file(reference_path):
            input_issues.append("reference image hash does not match preflight")
        if preflight_render_hash and preflight_render_hash != sha256_file(render_path):
            input_issues.append("render image hash does not match preflight")

    try:
        reference = Image.open(reference_path).convert("RGB")
        render = Image.open(render_path).convert("RGB")
    except Exception as exc:
        fail(f"Unable to read image: {exc}", 4)

    regions = regions_doc.get("regions", [])
    masks = gate_mask_boxes(config)
    thresholds = config.get("thresholds", {})
    required_thresholds = ("macroAnchorPx", "componentAnchorPx", "maxDiffPixels", "maxDiffPixelRatio", "pixelThreshold")
    configured = isinstance(thresholds, dict) and all(thresholds.get(key) is not None for key in required_thresholds)
    threshold = float(thresholds.get("pixelThreshold") or 0)

    try:
        different, comparable, diff_rgba = vectorized_diff(reference, render, masks, threshold)
    except RuntimeError as exc:
        fail(str(exc), 3)
    diff_pixels = int(different.sum())
    comparable_pixels = int(comparable.sum())
    diff_integral = integral_image(different)
    comparable_integral = integral_image(comparable)

    region_ids = {region.get("id") for region in regions if isinstance(region, dict)}
    parent_ids = {region.get("parent") for region in regions if isinstance(region, dict) and region.get("parent") in region_ids}
    mappings = mapping_index(structure_map)
    region_metrics = [
        metric_for_region(
            region,
            (reference.width, reference.height),
            diff_integral,
            comparable_integral,
            mappings.get(str(region.get("id"))),
            region.get("id") not in parent_ids,
        )
        for region in regions
        if isinstance(region, dict)
    ]
    leaf_metrics = [item for item in region_metrics if item.get("isLeaf")]
    mapped_leaves = [item for item in leaf_metrics if item.get("mappingState") == "verified"]
    matched_leaves = [item for item in mapped_leaves if item.get("matched")]
    reference_area = sum(item.get("referenceBbox", {}).get("width", 0) * item.get("referenceBbox", {}).get("height", 0) for item in leaf_metrics)
    missing_area = sum(item.get("referenceBbox", {}).get("width", 0) * item.get("referenceBbox", {}).get("height", 0) for item in leaf_metrics if not item.get("matched"))
    extra_regions = regions_doc.get("extraRegions", [])
    extra_rate = None
    if isinstance(extra_regions, list) and (len(matched_leaves) + len(extra_regions)):
        extra_rate = len(extra_regions) / (len(matched_leaves) + len(extra_regions))

    critical_failures: list[str] = []
    region_docs_by_id = {str(region.get("id")): region for region in regions if isinstance(region, dict)}
    for metric in leaf_metrics:
        if not metric.get("requiredForAcceptance"):
            metric["gate"] = {"required": False, "pass": None, "reasons": []}
            continue
        region_doc = region_docs_by_id.get(str(metric.get("id")), {})
        checks = region_doc.get("visualChecks") if isinstance(region_doc.get("visualChecks"), dict) else {}
        reasons: list[str] = []
        if checks.get("maxDiffPixelRatio") is None:
            reasons.append("required region has no local diff threshold")
        if structure_required and not metric.get("matched"):
            reasons.append("region is not mapped")
        if metric.get("localDiffDensity") is None:
            reasons.append("region has no comparable pixels")
        if checks.get("maxDiffPixelRatio") is not None and (metric.get("localDiffDensity") or 0) > float(checks["maxDiffPixelRatio"]):
            reasons.append("local diff density exceeds region limit")
        if structure_required and checks.get("maxAnchorPx") is not None and (metric.get("anchorError") is None or metric.get("anchorError") > float(checks["maxAnchorPx"])):
            reasons.append("anchor error exceeds region limit")
        metric["gate"] = {"required": True, "pass": not reasons, "reasons": reasons}
        critical_failures.extend(f"{metric.get('id')}: {reason}" for reason in reasons)

    structure_hashes_match = True
    if structure_map is not None:
        structure_hashes_match = structure_map.get("regionsHash") == stable_hash(regions_doc)
        if capture is not None:
            structure_hashes_match = structure_hashes_match and structure_map.get("captureHash") == stable_hash(capture)
    structure_verified = True if not structure_required else bool(leaf_metrics) and bool(
        structure_map
        and structure_map.get("status") == "verified"
        and structure_hashes_match
        and len(mapped_leaves) == len(leaf_metrics)
        and len(matched_leaves) == len(leaf_metrics)
    )
    structure_status = "not-applicable" if not structure_required else "verified" if structure_verified else "unavailable"
    structure = {
        "regionRecall": len(matched_leaves) / len(leaf_metrics) if leaf_metrics and mappings else None,
        "extraRegionRate": extra_rate,
        "missingAreaRate": missing_area / reference_area if reference_area and mappings else None,
        "referenceLeafRegionCount": len(leaf_metrics),
        "mappedLeafRegionCount": len(mapped_leaves),
        "matchedLeafRegionCount": len(matched_leaves),
        "hashesMatch": structure_hashes_match if structure_map else None,
        "status": structure_status,
    }

    same_size = reference.size == render.size
    anchor_values = [item["anchorError"] for item in leaf_metrics if item.get("anchorError") is not None]
    anchors_pass = True
    for metric in leaf_metrics:
        anchor = metric.get("anchorError")
        if anchor is None:
            continue
        role = str(metric.get("role", "")).lower()
        limit = thresholds.get("macroAnchorPx") if role in {"page", "shell", "main", "canvas"} else thresholds.get("componentAnchorPx")
        if limit is not None and anchor > float(limit):
            anchors_pass = False
    pixel_ratio = diff_pixels / comparable_pixels if comparable_pixels else 0.0
    pixel_pass = configured and comparable_pixels > 0 and diff_pixels <= int(thresholds["maxDiffPixels"]) and pixel_ratio <= float(thresholds["maxDiffPixelRatio"])

    blocking_issues = list(input_issues)
    if not configured:
        blocking_issues.append("all five thresholds are required")
    if not same_size:
        blocking_issues.append("reference and render image dimensions must match")
    if comparable_pixels <= 0:
        blocking_issues.append("there are no comparable pixels after masks")
    if workflow_mode == "acceptance" and critical_failures:
        blocking_issues.extend(critical_failures)
    if workflow_mode == "acceptance" and thresholds.get("maxExtraRegionRate") is not None and (extra_rate is None or extra_rate > float(thresholds["maxExtraRegionRate"])):
        blocking_issues.append("extra region rate exceeds configured threshold")
    if workflow_mode == "acceptance" and thresholds.get("maxMissingAreaRate") is not None and (structure.get("missingAreaRate") is None or structure["missingAreaRate"] > float(thresholds["maxMissingAreaRate"])):
        blocking_issues.append("missing region area rate exceeds configured threshold")
    if workflow_mode == "acceptance" and not leaf_metrics:
        blocking_issues.append("acceptance mode requires leaf regions")
    if workflow_mode == "acceptance" and structure_required and leaf_metrics and not structure_verified:
        blocking_issues.append("a verified structure map is required")

    accepted = bool(
        workflow_mode == "acceptance"
        and not blocking_issues
        and pixel_pass
        and anchors_pass
        and structure_verified
        and not critical_failures
    )
    if accepted:
        status = "accepted"
    elif workflow_mode == "acceptance" and blocking_issues:
        status = "blocked"
    elif not same_size or not configured or comparable_pixels <= 0:
        status = "blocked"
    else:
        status = "unresolved"

    limitations: list[str] = []
    for item in [*blocking_issues, *critical_failures]:
        if item not in limitations:
            limitations.append(item)
    if workflow_mode == "iteration":
        limitations.append("iteration mode never produces accepted")

    out.mkdir(parents=True, exist_ok=True)
    diff_path = out / "diff.png"
    json_path = out / "diff.json"

    report = {
        "schemaVersion": "1.0",
        "scenarioId": scenario,
        "status": status,
        "workflowMode": workflow_mode,
        "reference": str(reference_path),
        "render": str(render_path),
        "dimensions": {"reference": list(reference.size), "render": list(render.size), "same": same_size},
        "masks": {
            "excludedFromGate": masks,
            "configured": config.get("masks", []),
            "excludedPixelCount": int((~comparable).sum()),
        },
        "capture": {"provided": capture is not None, "readiness": capture.get("readiness") if capture else None},
        "preflight": {"provided": preflight is not None, "status": preflight.get("status") if preflight else None},
        "sourceHashes": {
            "referenceSha256": sha256_file(reference_path),
            "renderSha256": sha256_file(render_path),
            "captureHash": stable_hash(capture) if capture is not None else None,
            "structureMapHash": stable_hash(structure_map) if structure_map is not None else None,
        },
        "thresholds": thresholds,
        "thresholdsConfigured": configured,
        "pixel": {"diffPixels": diff_pixels, "comparablePixels": comparable_pixels, "pixelDiffRatio": pixel_ratio, "pass": pixel_pass},
        "anchors": {"values": anchor_values, "pass": anchors_pass},
        "structure": structure,
        "regions": region_metrics,
        "acceptedVarianceCandidates": [],
        "acceptanceGate": {
            "eligible": workflow_mode == "acceptance",
            "accepted": accepted,
            "blockingIssues": blocking_issues,
            "criticalRegionFailures": critical_failures,
        },
        "limitations": limitations,
    }
    try:
        atomic_write_json(json_path, report, "diff.schema.json")
    except (ContractError, OSError) as exc:
        fail(str(exc), 2)
    print(json.dumps({"status": status, "diff": str(diff_path), "report": str(json_path), "pixelDiffRatio": pixel_ratio}, ensure_ascii=False))
    return 0 if status in {"accepted", "unresolved"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
