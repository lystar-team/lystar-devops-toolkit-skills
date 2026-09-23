#!/usr/bin/env python3
"""Create a schema-valid lystar-ui-restore scenario workspace."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from restore_core import ContractError, atomic_write_json, load_json


def ensure_document(path: Path, document: dict, schema: str) -> None:
    if path.is_file():
        load_json(path, schema)
        return
    atomic_write_json(path, document, schema)


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a lystar-ui-restore scenario.")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--visual-root", default=".visual-restore")
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--reference-mode", default="screenshot", choices=["screenshot", "design-image", "local-live", "public-live", "source-code"])
    parser.add_argument("--target", default="web", choices=["web", "h5", "native-app", "miniprogram", "svg", "canvas", "webgl", "3d", "custom"])
    parser.add_argument("--capture-adapter", default="agent-browser")
    parser.add_argument("--route", default="/")
    parser.add_argument("--page-state", default="default")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--dpr", type=float, default=1.0)
    parser.add_argument("--workflow-mode", choices=["iteration", "acceptance"], default="iteration")
    args = parser.parse_args()

    project_root = Path(args.root).expanduser().resolve()
    visual_root = Path(args.visual_root).expanduser()
    if not visual_root.is_absolute():
        visual_root = project_root / visual_root
    scenario_root = visual_root.resolve() / "scenarios" / args.scenario_id
    for name in ("inspection", "renders", "diffs", "focus", "preflight", "matrix"):
        (scenario_root / name).mkdir(parents=True, exist_ok=True)

    config = {
        "schemaVersion": "1.0",
        "scenarioId": args.scenario_id,
        "referenceMode": args.reference_mode,
        "target": args.target,
        "captureAdapter": args.capture_adapter,
        "route": args.route,
        "workflowMode": args.workflow_mode,
        "disableAnimation": True,
        "viewport": {"width": args.width, "height": args.height, "deviceScaleFactor": args.dpr},
        "readiness": {"requireFonts": True, "requireImages": True, "failOnPageErrors": True},
        "assetPolicy": {"requireManifest": True, "requireReviewedAssets": False},
        "layerPolicy": {"requireVisualOwner": True},
        "screenshotMode": "viewport",
        "renderMode": "dom-css",
        "scroll": {"x": 0, "y": 0, "clip": None, "clipSpace": "css-viewport-px"},
        "masks": [],
        "thresholds": {
            "macroAnchorPx": None,
            "componentAnchorPx": None,
            "maxDiffPixels": None,
            "maxDiffPixelRatio": None,
            "pixelThreshold": None,
        },
    }
    fixture = {
        "schemaVersion": "1.0",
        "scenarioId": args.scenario_id,
        "route": args.route,
        "pageState": args.page_state,
        "apiFixture": None,
        "loginState": None,
        "freezeTime": None,
        "waitFor": [],
        "disableAnimation": True,
        "seed": None,
        "networkMode": "unknown",
        "stateActions": [],
    }
    documents = {
        "config.json": (config, "config.schema.json"),
        "fixture.json": (fixture, "fixture.schema.json"),
        "evidence.json": ({"schemaVersion": "1.0", "scenarioId": args.scenario_id, "evidence": []}, "evidence.schema.json"),
        "regions.json": ({"schemaVersion": "1.0", "scenarioId": args.scenario_id, "coordinateSystem": "reference-image-px", "extraRegions": [], "regions": []}, "regions.schema.json"),
        "assets.json": ({"schemaVersion": "1.0", "scenarioId": args.scenario_id, "assets": []}, "assets.schema.json"),
        "content.json": ({"schemaVersion": "1.0", "scenarioId": args.scenario_id, "contents": []}, "content.schema.json"),
        "tokens.json": ({"schemaVersion": "1.0", "tokens": {}}, "tokens.schema.json"),
        "decisions.json": ({
            "schemaVersion": "1.0",
            "scenarioId": args.scenario_id,
            "contract": {
                "scene": args.reference_mode,
                "goal": "待填写",
                "focusRegions": [],
                "allowedExpansion": ["structure", "style", "assets", "visible-state"],
                "preserve": ["route", "interface", "content", "interaction", "accessibility-semantics"],
                "stopCondition": "待填写"
            },
            "decisions": [],
            "unresolved": []
        }, "decisions.schema.json"),
    }
    try:
        for name, (document, schema) in documents.items():
            ensure_document(scenario_root / name, document, schema)
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    manifest = {
        "schemaVersion": "1.0",
        "scenarioId": args.scenario_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "scenarioRoot": str(scenario_root),
        "documents": sorted(documents),
    }
    manifest_path = scenario_root / "manifest.json"
    if not manifest_path.is_file():
        atomic_write_json(manifest_path, manifest)
    print(json.dumps({"status": "completed", "scenarioId": args.scenario_id, "scenarioRoot": str(scenario_root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
