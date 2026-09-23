from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from coordinate_model import build_transform, transform_bbox  # noqa: E402
from preflight_visual import asset_checks  # noqa: E402
from restore_core import ContractError, stable_hash, validate_document  # noqa: E402
from validate_patch import validate_patch_data  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_viewports_only_config_is_valid(self) -> None:
        config = {
            "schemaVersion": "1.0",
            "scenarioId": "s",
            "referenceMode": "screenshot",
            "target": "web",
            "captureAdapter": "agent-browser",
            "viewports": [{"id": "desktop", "width": 1280, "height": 720, "deviceScaleFactor": 1}],
            "screenshotMode": "viewport",
            "thresholds": {
                "macroAnchorPx": None,
                "componentAnchorPx": None,
                "maxDiffPixels": None,
                "maxDiffPixelRatio": None,
                "pixelThreshold": None,
            },
        }
        validate_document(config, "config.schema.json")

    def test_invalid_state_type_is_rejected(self) -> None:
        state = {
            "schemaVersion": "1.0",
            "scenarioId": "s",
            "reference": 1,
            "capture": "capture.json",
            "regions": "regions.json",
            "tokens": "tokens.json",
            "decisions": "decisions.json",
            "activeRegion": None,
            "currentRender": None,
            "diffReport": None,
            "status": "observing",
            "updatedAt": "2026-09-04T00:00:00Z",
        }
        with self.assertRaises(ContractError):
            validate_document(state, "state.schema.json")

    def test_coordinate_transform_uses_render_size(self) -> None:
        capture = {
            "viewport": {"width": 200, "height": 100, "deviceScaleFactor": 2},
            "readiness": {"imageSize": [400, 200]},
        }
        config = {"screenshotMode": "viewport"}
        transform = build_transform(capture, config)
        self.assertEqual(transform_bbox({"x": 10, "y": 5, "width": 20, "height": 10}, transform), {"x": 20.0, "y": 10.0, "width": 40.0, "height": 20.0})

    def test_patch_enforces_region_property_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "page.css"
            source.write_text(".page{}", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            adapter = {
                "schemaVersion": "1.0",
                "projectId": "p",
                "framework": "css",
                "target": "web",
                "captureAdapter": "agent-browser",
                "baseUrl": "http://127.0.0.1",
                "entry": "/",
                "allowedFiles": ["*.css"],
                "forbiddenFiles": [],
            }
            regions = {
                "schemaVersion": "1.0",
                "scenarioId": "s",
                "coordinateSystem": "reference-image-px",
                "regions": [{
                    "id": "page",
                    "parent": None,
                    "role": "page",
                    "bbox": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "visualOwner": "component",
                    "allowedLayers": ["component"],
                    "evidenceIds": ["E1"],
                    "allowedProperties": ["typography"],
                    "forbiddenChanges": ["geometry"],
                }],
            }
            evidence = {
                "schemaVersion": "1.0",
                "scenarioId": "s",
                "evidence": [{"id": "E1", "sourceType": "screenshot", "sourceRef": "ref.png", "state": "verified"}],
            }
            patch = {
                "schemaVersion": "1.0",
                "patchId": "P1",
                "scenarioId": "s",
                "regionId": "page",
                "visualOwner": "component",
                "problemType": "geometry",
                "propertyFamily": "geometry",
                "evidenceIds": ["E1"],
                "files": ["page.css"],
                "baseFileSha256": digest,
                "verification": {"status": "proposed"},
                "rollback": {"available": True, "method": "restore file"},
                "status": "proposed",
            }
            code, message, _ = validate_patch_data(patch, adapter, root, True, regions, evidence)
            self.assertEqual(code, 3)
            self.assertIn("allowedProperties", message)


class WorkflowTests(unittest.TestCase):
    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True)

    def test_reference_catalog_normalize_and_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scenario = root / ".visual-restore/scenarios/s"
            scenario.mkdir(parents=True)
            reference = root / "reference.png"
            render = root / "render.png"
            Image.new("RGB", (400, 200), (250, 250, 250)).save(reference)
            Image.new("RGB", (400, 200), (250, 250, 250)).save(render)
            manual = {
                "regions": [{
                    "id": "page",
                    "parent": None,
                    "role": "page",
                    "bbox": {"x": 0, "y": 0, "width": 400, "height": 200},
                    "visualOwner": "component",
                    "allowedLayers": ["component"],
                    "evidenceIds": ["E1"],
                    "allowedProperties": ["geometry", "surface"],
                    "forbiddenChanges": [],
                    "locator": {"selector": "#app"},
                }]
            }
            manual_path = root / "manual.json"
            manual_path.write_text(json.dumps(manual), encoding="utf-8")
            inspected = self.run_script("inspect_reference.py", "--reference", str(reference), "--scenario-dir", str(scenario), "--scenario-id", "s", "--regions", str(manual_path))
            self.assertEqual(inspected.returncode, 0, inspected.stderr)

            assets_dir = root / "assets"
            assets_dir.mkdir()
            Image.new("RGBA", (20, 10), (1, 2, 3, 128)).save(assets_dir / "a.png")
            catalog_path = scenario / "inspection/asset-catalog.json"
            catalog_args = ("--root", str(root), "--path", "assets", "--scenario-id", "s", "--out", str(catalog_path), "--assets-out", str(scenario / "assets.json"))
            first = self.run_script("asset_catalog.py", *catalog_args)
            second = self.run_script("asset_catalog.py", *catalog_args)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            second_result = json.loads(second.stdout.strip().splitlines()[-1])
            self.assertEqual(second_result["summary"]["cacheReusedCount"], 1)

            config = {
                "schemaVersion": "1.0",
                "scenarioId": "s",
                "referenceMode": "screenshot",
                "target": "web",
                "captureAdapter": "agent-browser",
                "workflowMode": "acceptance",
                "viewport": {"width": 200, "height": 100, "deviceScaleFactor": 2, "imageWidth": 400, "imageHeight": 200},
                "readiness": {"requireFonts": True, "requireImages": True, "failOnPageErrors": True},
                "assetPolicy": {"requireManifest": True, "requireReviewedAssets": False},
                "layerPolicy": {"requireVisualOwner": True},
                "screenshotMode": "viewport",
                "renderMode": "dom-css",
                "scroll": {"x": 0, "y": 0, "clip": None, "clipSpace": "css-viewport-px"},
                "masks": [],
                "thresholds": {"macroAnchorPx": 0, "componentAnchorPx": 0, "maxDiffPixels": 0, "maxDiffPixelRatio": 0, "pixelThreshold": 0},
            }
            config_path = scenario / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            capture = {
                "schemaVersion": "1.0",
                "scenarioId": "s",
                "referenceSource": "screenshot",
                "referenceEnvironment": {"knowledge": "unknown"},
                "renderEnvironment": {"knowledge": "known"},
                "viewport": config["viewport"],
                "route": "http://example.test",
                "pageState": "default",
                "scroll": {"x": 0, "y": 0, "clip": None},
                "runtimeOverrides": {},
                "runtimeState": {"requested": {}, "applied": {}, "observed": {}, "unsupported": []},
                "readiness": {
                    "status": "ready",
                    "url": "http://example.test",
                    "title": "test",
                    "readySelector": None,
                    "readySelectorMatched": None,
                    "fonts": "loaded",
                    "imagesComplete": True,
                    "bodyTextLength": 1,
                    "visibleElementCount": 1,
                    "loginDetected": False,
                    "blankDetected": False,
                    "screenshot": str(render),
                    "imageSha256": hashlib.sha256(render.read_bytes()).hexdigest(),
                    "imageSize": [400, 200],
                    "imageStats": {},
                    "reasons": [],
                },
                "coordinateSystem": "render-image-px with css-viewport evidence",
                "renderMode": "dom-css",
                "workflowMode": "acceptance",
            }
            capture_path = scenario / "capture.json"
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            geometry = {
                "schemaVersion": "1.0",
                "scenarioId": "s",
                "capturedAt": "2026-09-04T00:00:00Z",
                "url": "http://example.test",
                "captureHash": stable_hash(capture),
                "coordinateSystem": "css-viewport-px",
                "viewport": {"width": 200, "height": 100, "devicePixelRatio": 2, "scrollX": 0, "scrollY": 0},
                "document": {},
                "regions": {"page": {"id": "page", "selector": "#app", "matched": True, "matchCount": 1, "matches": [{"bbox": {"x": 0, "y": 0, "width": 200, "height": 100}, "visible": True}]}},
                "discovered": [],
                "unmatchedSelectors": [],
                "requiredUnmatchedSelectors": [],
                "status": "pass",
            }
            geometry_path = scenario / "geometry.json"
            geometry_path.write_text(json.dumps(geometry), encoding="utf-8")
            structure_path = scenario / "structure-map.json"
            normalized = self.run_script("normalize_geometry.py", "--geometry", str(geometry_path), "--capture", str(capture_path), "--config", str(config_path), "--regions", str(scenario / "regions.json"), "--out", str(structure_path))
            self.assertEqual(normalized.returncode, 0, normalized.stderr)
            preflight_path = scenario / "preflight.json"
            preflight_path.write_text(json.dumps({
                "schemaVersion": "1.0", "scenarioId": "s", "workflowMode": "acceptance", "status": "pass",
                "root": str(root), "reference": str(reference), "render": str(render),
                "blocked": [], "warnings": [], "details": {
                    "reference": {"sha256": hashlib.sha256(reference.read_bytes()).hexdigest()},
                    "render": {"sha256": hashlib.sha256(render.read_bytes()).hexdigest()},
                }            }), encoding="utf-8")
            compared = self.run_script("compare_visual.py", "--reference", str(reference), "--render", str(render), "--regions", str(scenario / "regions.json"), "--structure-map", str(structure_path), "--config", str(config_path), "--capture", str(capture_path), "--preflight", str(preflight_path), "--out", str(scenario / "diff"))
            self.assertEqual(compared.returncode, 0, compared.stderr)
            result = json.loads(compared.stdout.strip().splitlines()[-1])
            self.assertEqual(result["status"], "accepted")
    def test_iteration_compare_never_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.png"
            render = root / "render.png"
            Image.new("RGB", (20, 20), (10, 20, 30)).save(reference)
            Image.new("RGB", (20, 20), (10, 20, 30)).save(render)
            config = {
                "schemaVersion": "1.0", "scenarioId": "iteration", "referenceMode": "screenshot",
                "target": "web", "captureAdapter": "agent-browser", "workflowMode": "iteration",
                "viewport": {"width": 20, "height": 20, "deviceScaleFactor": 1},
                "screenshotMode": "viewport", "renderMode": "dom-css", "masks": [],
                "thresholds": {"macroAnchorPx": 0, "componentAnchorPx": 0, "maxDiffPixels": 0, "maxDiffPixelRatio": 0, "pixelThreshold": 0},
            }
            regions = {"schemaVersion": "1.0", "scenarioId": "iteration", "coordinateSystem": "reference-image-px", "regions": []}
            config_path = root / "config.json"; regions_path = root / "regions.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            regions_path.write_text(json.dumps(regions), encoding="utf-8")
            compared = self.run_script("compare_visual.py", "--reference", str(reference), "--render", str(render), "--regions", str(regions_path), "--config", str(config_path), "--out", str(root / "diff"))
            self.assertEqual(compared.returncode, 0, compared.stderr)
            result = json.loads(compared.stdout.strip().splitlines()[-1])
            self.assertEqual(result["status"], "unresolved")
            report = json.loads((root / "diff/diff.json").read_text())
            self.assertFalse(report["acceptanceGate"]["eligible"])

    def test_acceptance_compare_requires_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.png"; render = root / "render.png"
            Image.new("RGB", (20, 20), (10, 20, 30)).save(reference)
            Image.new("RGB", (20, 20), (10, 20, 30)).save(render)
            config = {
                "schemaVersion": "1.0", "scenarioId": "acceptance", "referenceMode": "screenshot",
                "target": "web", "captureAdapter": "agent-browser", "workflowMode": "acceptance",
                "viewport": {"width": 20, "height": 20, "deviceScaleFactor": 1},
                "screenshotMode": "viewport", "renderMode": "dom-css", "masks": [],
                "thresholds": {"macroAnchorPx": 0, "componentAnchorPx": 0, "maxDiffPixels": 0, "maxDiffPixelRatio": 0, "pixelThreshold": 0},
            }
            regions = {"schemaVersion": "1.0", "scenarioId": "acceptance", "coordinateSystem": "reference-image-px", "regions": []}
            config_path = root / "config.json"; regions_path = root / "regions.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            regions_path.write_text(json.dumps(regions), encoding="utf-8")
            compared = self.run_script("compare_visual.py", "--reference", str(reference), "--render", str(render), "--regions", str(regions_path), "--config", str(config_path), "--out", str(root / "diff"))
            self.assertEqual(compared.returncode, 3)
            result = json.loads(compared.stdout.strip().splitlines()[-1])
            self.assertEqual(result["status"], "blocked")

    def test_inspect_preserves_contract_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scenario = root / "visual/scenarios/s"
            initialized = self.run_script("init_scenario.py", "--root", str(root), "--visual-root", str(root / "visual"), "--scenario-id", "s")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            decisions_path = scenario / "decisions.json"
            decisions = json.loads(decisions_path.read_text())
            decisions["contract"]["goal"] = "keep-this-contract"
            decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
            reference = root / "reference.png"; Image.new("RGB", (10, 10), (1, 2, 3)).save(reference)
            inspected = self.run_script("inspect_reference.py", "--reference", str(reference), "--scenario-dir", str(scenario), "--scenario-id", "s")
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(json.loads(decisions_path.read_text())["contract"]["goal"], "keep-this-contract")

    def test_catalog_preserves_reviewed_asset_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); assets_dir = root / "assets"; assets_dir.mkdir()
            Image.new("RGBA", (20, 10), (1, 2, 3, 128)).save(assets_dir / "frame.png")
            catalog = root / "catalog.json"; assets = root / "assets.json"
            args = ("--root", str(root), "--path", "assets", "--scenario-id", "s", "--out", str(catalog), "--assets-out", str(assets))
            first = self.run_script("asset_catalog.py", *args)
            self.assertEqual(first.returncode, 0, first.stderr)
            document = json.loads(assets.read_text())
            document["assets"][0].update({"regionId": "header", "visualOwner": "image", "state": "verified", "reviewed": True})
            assets.write_text(json.dumps(document), encoding="utf-8")
            second = self.run_script("asset_catalog.py", *args)
            self.assertEqual(second.returncode, 0, second.stderr)
            preserved = json.loads(assets.read_text())["assets"][0]
            self.assertEqual(preserved["regionId"], "header")
            self.assertEqual(preserved["visualOwner"], "image")
            self.assertEqual(preserved["state"], "verified")
            self.assertTrue(preserved["reviewed"])

    def test_runtime_asset_uses_evidence_without_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = {
                "schemaVersion": "1.0", "scenarioId": "s", "assets": [{
                    "assetId": "chart", "regionId": "chart", "evidenceIds": ["E1"],
                    "type": "canvas", "renderMode": "canvas", "visualOwner": "canvas",
                    "sourceKind": "runtime", "sourcePath": None, "state": "verified", "reviewed": True,
                }]
            }
            validate_document(assets, "assets.schema.json")
            evidence = {"schemaVersion": "1.0", "scenarioId": "s", "evidence": [{"id": "E1", "sourceType": "runtime", "sourceRef": "capture", "state": "verified"}]}
            validate_document(evidence, "evidence.schema.json")
            _, blocked, warnings = asset_checks(root, assets, True, True, True, True, None, evidence)
            self.assertEqual(blocked, [])
    def test_normalize_rejects_mismatched_capture_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {
                "schemaVersion": "1.0", "scenarioId": "s", "referenceMode": "screenshot",
                "target": "web", "captureAdapter": "agent-browser", "workflowMode": "acceptance",
                "viewport": {"width": 20, "height": 20, "deviceScaleFactor": 1},
                "screenshotMode": "viewport", "renderMode": "dom-css", "thresholds": {
                    "macroAnchorPx": 0, "componentAnchorPx": 0, "maxDiffPixels": 0,
                    "maxDiffPixelRatio": 0, "pixelThreshold": 0,
                },
            }
            capture = {
                "schemaVersion": "1.0", "scenarioId": "s", "workflowMode": "acceptance",
                "referenceSource": "screenshot", "referenceEnvironment": {"knowledge": "known"},
                "renderEnvironment": {"knowledge": "known"}, "viewport": config["viewport"],
                "route": "http://expected.test", "scroll": {"x": 0, "y": 0, "clip": None},
                "coordinateSystem": "render-image-px", "renderMode": "dom-css",
            }
            regions = {"schemaVersion": "1.0", "scenarioId": "s", "coordinateSystem": "reference-image-px", "regions": [{
                "id": "page", "parent": None, "role": "page", "bbox": {"x": 0, "y": 0, "width": 20, "height": 20},
                "visualOwner": "component", "allowedLayers": ["component"], "evidenceIds": [],
                "allowedProperties": [], "forbiddenChanges": [],
            }]}
            geometry = {
                "schemaVersion": "1.0", "scenarioId": "s", "capturedAt": "2026-09-05T00:00:00Z",
                "url": "http://other.test", "captureHash": stable_hash(capture), "coordinateSystem": "css-viewport-px",
                "viewport": {"width": 20, "height": 20, "devicePixelRatio": 1, "scrollX": 0, "scrollY": 0},
                "document": {}, "regions": {}, "status": "pass",
            }
            paths = {}
            for name, value in (("config.json", config), ("capture.json", capture), ("regions.json", regions), ("geometry.json", geometry)):
                path = root / name; path.write_text(json.dumps(value), encoding="utf-8"); paths[name] = path
            normalized = self.run_script(
                "normalize_geometry.py", "--geometry", str(paths["geometry.json"]), "--capture", str(paths["capture.json"]),
                "--config", str(paths["config.json"]), "--regions", str(paths["regions.json"]), "--out", str(root / "structure.json"),
            )
            self.assertEqual(normalized.returncode, 3)
            self.assertEqual(json.loads(normalized.stdout.strip().splitlines()[-1])["status"], "blocked")

    def test_key_region_gate_rejects_local_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.png"; render = root / "render.png"
            Image.new("RGB", (20, 20), (10, 20, 30)).save(reference)
            image = Image.new("RGB", (20, 20), (10, 20, 30))
            for y in range(4):
                for x in range(4):
                    image.putpixel((x, y), (200, 30, 40))
            image.save(render)
            config = {
                "schemaVersion": "1.0", "scenarioId": "s", "referenceMode": "screenshot",
                "target": "web", "captureAdapter": "agent-browser", "workflowMode": "acceptance",
                "viewport": {"width": 20, "height": 20, "deviceScaleFactor": 1},
                "screenshotMode": "viewport", "renderMode": "dom-css", "thresholds": {
                    "macroAnchorPx": 0, "componentAnchorPx": 0, "maxDiffPixels": 400,
                    "maxDiffPixelRatio": 1, "pixelThreshold": 0,
                },
            }
            regions = {"schemaVersion": "1.0", "scenarioId": "s", "coordinateSystem": "reference-image-px", "regions": [{
                "id": "hero", "parent": None, "role": "component", "bbox": {"x": 0, "y": 0, "width": 4, "height": 4},
                "visualOwner": "component", "allowedLayers": ["component"], "evidenceIds": [],
                "allowedProperties": [], "forbiddenChanges": [], "requiredForAcceptance": True,
                "visualChecks": {"maxDiffPixelRatio": 0.0},
            }]}
            capture = {
                "schemaVersion": "1.0", "scenarioId": "s", "workflowMode": "acceptance",
                "referenceSource": "screenshot", "referenceEnvironment": {"knowledge": "known"},
                "renderEnvironment": {"knowledge": "known"}, "viewport": config["viewport"],
                "route": "http://example.test", "scroll": {"x": 0, "y": 0, "clip": None},
                "runtimeState": {"requested": {}, "applied": {}, "observed": {}, "unsupported": []},
                "readiness": {"status": "ready", "url": "http://example.test", "fonts": "loaded", "imagesComplete": True,
                    "loginDetected": False, "blankDetected": False, "screenshot": str(render),
                    "imageSha256": hashlib.sha256(render.read_bytes()).hexdigest(), "imageSize": [20, 20], "reasons": []},
                "coordinateSystem": "render-image-px", "renderMode": "dom-css",
            }
            structure = {
                "schemaVersion": "1.0", "scenarioId": "s", "workflowMode": "acceptance", "createdAt": "2026-09-05T00:00:00Z",
                "status": "verified", "captureHash": stable_hash(capture), "geometryHash": "geometry", "regionsHash": stable_hash(regions),
                "transform": {"sourceSpace": "css-viewport-px", "targetSpace": "render-image-px", "screenshotMode": "viewport",
                    "scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0,
                    "imageSize": {"width": 20, "height": 20}, "cssViewport": {"width": 20, "height": 20}},
                "regions": [{"regionId": "hero", "matched": True, "matchCount": 1, "visible": True,
                    "renderBbox": {"x": 0, "y": 0, "width": 4, "height": 4}, "sourceSelector": "#hero", "state": "verified"}],
                "unresolvedRegionIds": [],
            }
            preflight = {"schemaVersion": "1.0", "scenarioId": "s", "workflowMode": "acceptance", "status": "pass",
                "root": str(root), "reference": str(reference), "render": str(render), "blocked": [], "warnings": [],
                "details": {"reference": {"sha256": hashlib.sha256(reference.read_bytes()).hexdigest()},
                    "render": {"sha256": hashlib.sha256(render.read_bytes()).hexdigest()}}}
            paths = {}
            for name, value in (("config.json", config), ("regions.json", regions), ("capture.json", capture), ("structure.json", structure), ("preflight.json", preflight)):
                path = root / name; path.write_text(json.dumps(value), encoding="utf-8"); paths[name] = path
            compared = self.run_script("compare_visual.py", "--reference", str(reference), "--render", str(render),
                "--regions", str(paths["regions.json"]), "--structure-map", str(paths["structure.json"]),
                "--config", str(paths["config.json"]), "--capture", str(paths["capture.json"]),
                "--preflight", str(paths["preflight.json"]), "--out", str(root / "diff"))
            self.assertEqual(compared.returncode, 3)
            result = json.loads((root / "diff/diff.json").read_text())
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(result["acceptanceGate"]["criticalRegionFailures"])

    def test_preflight_acceptance_binds_reference_and_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.png"
            render = root / "render.png"
            Image.new("RGB", (20, 20), (10, 20, 30)).save(reference)
            Image.new("RGB", (20, 20), (10, 20, 30)).save(render)
            config = {
                "schemaVersion": "1.0", "scenarioId": "s", "referenceMode": "screenshot",
                "target": "web", "captureAdapter": "agent-browser", "workflowMode": "acceptance",
                "viewport": {"width": 20, "height": 20, "deviceScaleFactor": 1},
                "readiness": {"requireFonts": False, "requireImages": False, "failOnPageErrors": False},
                "screenshotMode": "viewport", "renderMode": "dom-css", "thresholds": {
                    "macroAnchorPx": 0, "componentAnchorPx": 0, "maxDiffPixels": 0,
                    "maxDiffPixelRatio": 0, "pixelThreshold": 0,
                },
            }
            capture = {
                "schemaVersion": "1.0", "scenarioId": "s", "workflowMode": "acceptance",
                "referenceSource": "screenshot", "referenceEnvironment": {"knowledge": "known"},
                "renderEnvironment": {"knowledge": "known"}, "viewport": config["viewport"],
                "route": "http://example.test", "scroll": {"x": 0, "y": 0, "clip": None},
                "runtimeState": {"requested": {}, "applied": {}, "observed": {}, "unsupported": []},
                "readiness": {
                    "status": "ready", "url": "http://example.test", "title": "test",
                    "readySelector": None, "readySelectorMatched": None, "fonts": "loaded",
                    "imagesComplete": True, "bodyTextLength": 1, "visibleElementCount": 1,
                    "loginDetected": False, "blankDetected": False, "screenshot": str(render),
                    "imageSha256": hashlib.sha256(render.read_bytes()).hexdigest(), "imageSize": [20, 20],
                    "imageStats": {}, "reasons": [],
                },
                "coordinateSystem": "render-image-px with css-viewport evidence", "renderMode": "canvas",
            }
            files = {
                "config.json": config,
                "capture.json": capture,
                "reference-meta.json": {
                    "schemaVersion": "1.0", "scenarioId": "s", "reference": str(reference),
                    "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(), "width": 20, "height": 20,
                    "mode": "RGBA", "crops": [], "samples": [], "coordinateSystem": "reference-image-px",
                },
            }
            paths = {}
            for name, value in files.items():
                path = root / name; path.write_text(json.dumps(value), encoding="utf-8"); paths[name] = path
            first = self.run_script(
                "preflight_visual.py", "--root", str(root), "--reference", str(reference), "--render", str(render),
                "--reference-meta", str(paths["reference-meta.json"]), "--capture", str(paths["capture.json"]),
                "--config", str(paths["config.json"]), "--mode", "acceptance", "--out", str(root / "preflight"),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout.strip().splitlines()[-1])["status"], "pass")
            Image.new("RGB", (20, 20), (99, 99, 99)).save(render)
            second = self.run_script(
                "preflight_visual.py", "--root", str(root), "--reference", str(reference), "--render", str(render),
                "--reference-meta", str(paths["reference-meta.json"]), "--capture", str(paths["capture.json"]),
                "--config", str(paths["config.json"]), "--mode", "acceptance", "--out", str(root / "preflight-2"),
            )
            self.assertEqual(second.returncode, 3)
            self.assertIn("render image hash does not match capture", json.loads(second.stdout.strip().splitlines()[-1])["blocked"])


if __name__ == "__main__":
    unittest.main()
