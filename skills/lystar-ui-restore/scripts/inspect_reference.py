#!/usr/bin/env python3
"""Prepare reference evidence and schema-valid scenario documents."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from restore_core import ContractError, atomic_write_json, load_json, sha256_file


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def load_untyped_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"JSON file not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON {path}: {exc}")


def load_existing_document(path: Path, schema_name: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return load_json(path, schema_name)
    except ContractError as exc:
        fail(str(exc), 2)


def parse_box(value: str) -> dict[str, float]:
    parts = value.split(",")
    if len(parts) != 4:
        fail(f"Invalid box, expected x,y,width,height: {value}")
    try:
        x, y, width, height = (float(part) for part in parts)
    except ValueError:
        fail(f"Invalid box, expected numbers: {value}")
    return {"x": x, "y": y, "width": width, "height": height}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create lystar-ui-restore evidence files from a reference image.")
    parser.add_argument("--reference", required=True, help="Reference PNG/JPEG path")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--scenario-dir", help="Scenario directory; inspection artifacts and scenario documents use separate paths")
    destination.add_argument("--out", help="Legacy output directory")
    parser.add_argument("--scenario-id", default="reference", help="Scenario identifier")
    parser.add_argument("--regions", help="Manual regions JSON path")
    parser.add_argument("--crop", action="append", default=[], help="Crop box x,y,width,height; repeatable")
    parser.add_argument("--sample", action="append", default=[], help="Pixel sample x,y; repeatable")
    args = parser.parse_args()

    reference = Path(args.reference).expanduser().resolve()
    if not reference.is_file():
        fail(f"Reference image not found: {reference}")
    scenario_dir = Path(args.scenario_dir).expanduser().resolve() if args.scenario_dir else None
    inspection_dir = scenario_dir / "inspection" if scenario_dir else Path(args.out).expanduser().resolve()
    document_dir = scenario_dir or inspection_dir

    try:
        from PIL import Image
    except ImportError:
        fail("Pillow is required by inspect_reference.py", 3)
    try:
        image = Image.open(reference).convert("RGBA")
    except Exception as exc:
        fail(f"Unable to read reference image: {exc}", 4)

    inspection_dir.mkdir(parents=True, exist_ok=True)
    document_dir.mkdir(parents=True, exist_ok=True)
    existing_regions_document = load_existing_document(document_dir / "regions.json", "regions.schema.json") if scenario_dir else None
    existing_evidence_document = load_existing_document(document_dir / "evidence.json", "evidence.schema.json") if scenario_dir else None
    existing_content_document = load_existing_document(document_dir / "content.json", "content.schema.json") if scenario_dir else None
    existing_assets_document = load_existing_document(document_dir / "assets.json", "assets.schema.json") if scenario_dir else None
    existing_tokens_document = load_existing_document(document_dir / "tokens.json", "tokens.schema.json") if scenario_dir else None
    existing_decisions_document = load_existing_document(document_dir / "decisions.json", "decisions.schema.json") if scenario_dir else None
    crops_dir = inspection_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    crop_records = []
    for index, raw_box in enumerate(args.crop, start=1):
        parsed = parse_box(raw_box)
        left = max(0, int(parsed["x"]))
        top = max(0, int(parsed["y"]))
        right = min(image.width, left + max(0, int(parsed["width"])))
        bottom = min(image.height, top + max(0, int(parsed["height"])))
        if right <= left or bottom <= top:
            fail(f"Crop outside image: {raw_box}")
        crop_path = crops_dir / f"crop-{index:03d}.png"
        image.crop((left, top, right, bottom)).save(crop_path)
        crop_records.append({"path": str(crop_path), "bbox": {"x": left, "y": top, "width": right - left, "height": bottom - top}})

    samples = []
    for raw_sample in args.sample:
        parts = raw_sample.split(",")
        if len(parts) != 2:
            fail(f"Invalid sample, expected x,y: {raw_sample}")
        try:
            x, y = (int(part) for part in parts)
        except ValueError:
            fail(f"Invalid sample, expected integers: {raw_sample}")
        if not (0 <= x < image.width and 0 <= y < image.height):
            fail(f"Sample outside image: {raw_sample}")
        samples.append({"x": x, "y": y, "rgba": list(image.getpixel((x, y)))})

    regions: list[Any] = []
    if args.regions:
        raw_regions = load_untyped_json(Path(args.regions).expanduser().resolve())
        if isinstance(raw_regions, dict):
            regions = raw_regions.get("regions", [])
        elif isinstance(raw_regions, list):
            regions = raw_regions
        else:
            fail("Manual regions JSON must be an object or array")
        if not isinstance(regions, list):
            fail("Manual regions JSON field 'regions' must be an array")
    elif existing_regions_document:
        regions = list(existing_regions_document.get("regions", []))

    existing_region_by_id = {
        str(item.get("id")): item
        for item in (existing_regions_document or {}).get("regions", [])
        if isinstance(item, dict) and item.get("id")
    }
    existing_evidence_by_id = {
        str(item.get("id")): item
        for item in (existing_evidence_document or {}).get("evidence", [])
        if isinstance(item, dict) and item.get("id")
    }
    evidence_by_id: dict[str, dict[str, Any]] = dict(existing_evidence_by_id)
    normalized_regions = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            fail(f"Region {index} must be an object")
        region_id = str(region.get("id") or f"region-{index:03d}")
        normalized = dict(existing_region_by_id.get(region_id, {}))
        normalized.update(region)
        normalized.setdefault("id", region_id)
        normalized.setdefault("parent", None)
        normalized.setdefault("role", "unknown")
        normalized.setdefault("visualOwner", "unknown")
        normalized.setdefault("allowedLayers", [])
        normalized.setdefault("allowedProperties", [])
        normalized.setdefault("forbiddenChanges", [])
        normalized.setdefault("evidenceIds", [f"E-REGION-{index:03d}"])
        bbox = normalized.get("bbox")
        if bbox is None:
            fail(f"Region {normalized['id']} is missing bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            bbox = {"x": bbox[0], "y": bbox[1], "width": bbox[2], "height": bbox[3]}
            normalized["bbox"] = bbox
        normalized_regions.append(normalized)
        for evidence_id in normalized["evidenceIds"]:
            candidate = dict(existing_evidence_by_id.get(evidence_id, {}))
            candidate.update({
                "id": evidence_id,
                "objectType": normalized.get("role", "region"),
                "sourceType": "screenshot",
                "sourceRef": reference.name,
                "bbox": bbox,
                "state": candidate.get("state", "verified"),
                "confidence": candidate.get("confidence", 1),
                "notes": candidate.get("notes", "Manual region annotation"),
            })
            evidence_by_id[evidence_id] = candidate

    documents: list[tuple[Path, dict[str, Any], str]] = [
        (inspection_dir / "reference-meta.json", {
            "schemaVersion": "1.0",
            "scenarioId": args.scenario_id,
            "reference": str(reference),
            "sha256": sha256_file(reference),
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "crops": crop_records,
            "samples": samples,
            "coordinateSystem": "reference-image-px",
        }, "reference-meta.schema.json"),
        (document_dir / "regions.json", {
            "schemaVersion": "1.0",
            "scenarioId": args.scenario_id,
            "coordinateSystem": "reference-image-px",
            "extraRegions": (existing_regions_document or {}).get("extraRegions", []),
            "regions": normalized_regions,
        }, "regions.schema.json"),
        (document_dir / "evidence.json", {
            "schemaVersion": "1.0",
            "scenarioId": args.scenario_id,
            "evidence": list(evidence_by_id.values()),
        }, "evidence.schema.json"),
        (document_dir / "content.json", existing_content_document or {"schemaVersion": "1.0", "scenarioId": args.scenario_id, "contents": []}, "content.schema.json"),
        (document_dir / "assets.json", existing_assets_document or {"schemaVersion": "1.0", "scenarioId": args.scenario_id, "assets": []}, "assets.schema.json"),
        (document_dir / "tokens.json", existing_tokens_document or {"schemaVersion": "1.0", "tokens": {}}, "tokens.schema.json"),
        (document_dir / "decisions.json", existing_decisions_document or {
            "schemaVersion": "1.0",
            "scenarioId": args.scenario_id,
            "contract": None,
            "decisions": [],
            "unresolved": [],
        }, "decisions.schema.json"),
    ]
    try:
        for path, value, schema in documents:
            atomic_write_json(path, value, schema)
    except (ContractError, OSError) as exc:
        fail(str(exc), 2)

    print(json.dumps({
        "status": "completed",
        "scenarioId": args.scenario_id,
        "reference": str(reference),
        "inspection": str(inspection_dir),
        "documents": str(document_dir),
        "width": image.width,
        "height": image.height,
        "regionCount": len(normalized_regions),
        "evidenceCount": len(evidence_by_id),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
