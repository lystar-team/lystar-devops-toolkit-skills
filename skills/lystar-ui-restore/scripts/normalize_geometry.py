#!/usr/bin/env python3
"""Normalize runtime geometry into screenshot image coordinates."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coordinate_model import build_transform, transform_bbox
from restore_core import ContractError, atomic_write_json, close_number, load_json, require_same_scenario, resolve_workflow_mode, stable_hash, urls_match


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize geometry into render image coordinates.")
    parser.add_argument("--geometry", required=True)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--regions", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--viewport-id")
    args = parser.parse_args()

    try:
        geometry = load_json(Path(args.geometry).expanduser().resolve(), "geometry.schema.json")
        capture = load_json(Path(args.capture).expanduser().resolve(), "capture.schema.json")
        config = load_json(Path(args.config).expanduser().resolve(), "config.schema.json")
        regions_doc = load_json(Path(args.regions).expanduser().resolve(), "regions.schema.json")
        scenario = require_same_scenario([
            ("geometry", geometry), ("capture", capture), ("config", config), ("regions", regions_doc)
        ]) or "scenario"
        workflow_mode = resolve_workflow_mode(config)
    except ContractError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    try:
        expected_url = (capture.get("readiness") or {}).get("url") or capture.get("route")
        if capture.get("workflowMode") not in (None, workflow_mode):
            raise ContractError(f"capture workflowMode is {capture.get('workflowMode')}, expected {workflow_mode}")
        if expected_url and geometry.get("url") and not urls_match(geometry.get("url"), expected_url):
            raise ContractError(f"geometry URL does not match capture URL: geometry={geometry.get('url')}, capture={expected_url}")
        capture_viewport = capture.get("viewport") or {}
        geometry_viewport = geometry.get("viewport") or {}
        if not all(key in geometry_viewport for key in ("width", "height", "devicePixelRatio")):
            raise ContractError("geometry.viewport must include width, height and devicePixelRatio")
        if not close_number(geometry_viewport.get("width"), capture_viewport.get("width")) or not close_number(geometry_viewport.get("height"), capture_viewport.get("height")):
            raise ContractError("geometry viewport does not match capture viewport")
        if not close_number(geometry_viewport.get("devicePixelRatio"), capture_viewport.get("deviceScaleFactor")):
            raise ContractError("geometry DPR does not match capture deviceScaleFactor")
        capture_scroll = capture.get("scroll") or {}
        if not close_number(geometry_viewport.get("scrollX", 0), capture_scroll.get("x", 0)) or not close_number(geometry_viewport.get("scrollY", 0), capture_scroll.get("y", 0)):
            raise ContractError("geometry scroll position does not match capture scroll")
        if workflow_mode == "acceptance" and geometry.get("captureHash") != stable_hash(capture):
            raise ContractError("acceptance geometry must contain the hash of its capture")
        if geometry.get("captureHash") and geometry.get("captureHash") != stable_hash(capture):
            raise ContractError("geometry captureHash does not match capture")
        transform = build_transform(capture, config)
    except ContractError as exc:
        print(json.dumps({"status": "blocked", "message": str(exc)}, ensure_ascii=False))
        return 3

    probes = geometry.get("regions", {})
    mappings: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for region in regions_doc.get("regions", []):
        region_id = str(region["id"])
        probe = probes.get(region_id) if isinstance(probes, dict) else None
        matches = probe.get("matches", []) if isinstance(probe, dict) else []
        mapping: dict[str, Any] = {
            "regionId": region_id,
            "matched": False,
            "matchCount": int(probe.get("matchCount", 0)) if isinstance(probe, dict) else 0,
            "visible": None,
            "renderBbox": None,
            "sourceSelector": probe.get("selector") if isinstance(probe, dict) else None,
            "state": "unavailable",
        }
        if isinstance(matches, list) and len(matches) == 1 and isinstance(matches[0], dict):
            runtime_bbox = matches[0].get("bbox")
            render_bbox = transform_bbox(runtime_bbox, transform)
            visible = bool(matches[0].get("visible"))
            mapping.update({
                "matched": visible and render_bbox is not None,
                "visible": visible,
                "renderBbox": render_bbox,
                "state": "verified" if visible and render_bbox is not None else "unavailable",
            })
        else:
            unresolved.append(region_id)
        mappings.append(mapping)

    required_unmatched = set(geometry.get("requiredUnmatchedSelectors", []))
    verified = all(item["state"] == "verified" for item in mappings) and not required_unmatched
    result = {
        "schemaVersion": "1.0",
        "scenarioId": scenario,
        "viewportId": args.viewport_id,
        "workflowMode": workflow_mode,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "verified" if verified else "unresolved",
        "captureHash": stable_hash(capture),
        "geometryHash": stable_hash(geometry),
        "regionsHash": stable_hash(regions_doc),
        "transform": transform,
        "regions": mappings,
        "unresolvedRegionIds": sorted(set(unresolved) | required_unmatched),
    }
    try:
        output = Path(args.out).expanduser().resolve()
        atomic_write_json(output, result, "structure-map.schema.json")
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "structureMap": str(output), "regions": len(mappings)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
