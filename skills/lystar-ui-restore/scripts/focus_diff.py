#!/usr/bin/env python3
"""Create readable focus crops from a lystar-ui-restore diff report."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def box(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        return (int(float(value["x"])), int(float(value["y"])), int(float(value["width"])), int(float(value["height"])))
    except (KeyError, TypeError, ValueError):
        return None


def safe_name(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "region"))
    return name.strip("-") or "region"


def crop(image: Any, bounds: tuple[int, int, int, int], padding: int) -> Any:
    left, top, width, height = bounds
    return image.crop((max(0, left - padding), max(0, top - padding), min(image.width, left + width + padding), min(image.height, top + height + padding)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate focus crops for the highest-difference UI regions.")
    parser.add_argument("--diff-report", required=True, help="diff.json from compare_visual.py")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--render", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--padding", type=int, default=12)
    args = parser.parse_args()

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print(json.dumps({"status": "blocked", "message": "Pillow is required by focus_diff.py"}, ensure_ascii=False))
        return 3

    try:
        report = load_json(Path(args.diff_report).expanduser().resolve())
        reference = Image.open(Path(args.reference).expanduser().resolve()).convert("RGB")
        render = Image.open(Path(args.render).expanduser().resolve()).convert("RGB")
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    metrics = report.get("regions", [])
    if not isinstance(metrics, list):
        print(json.dumps({"status": "error", "message": "diff report regions must be an array"}, ensure_ascii=False))
        return 2
    ranked = [item for item in metrics if isinstance(item, dict) and item.get("isLeaf") and box(item.get("referenceBbox"))]
    ranked.sort(key=lambda item: (float(item.get("localDiffDensity") or 0), float(item.get("anchorError") or 0)), reverse=True)

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    rows: list[Any] = []
    for index, metric in enumerate(ranked[: max(0, args.limit)], start=1):
        reference_bounds = box(metric.get("referenceBbox"))
        render_bounds = box(metric.get("renderBbox")) or reference_bounds
        if reference_bounds is None or render_bounds is None:
            continue
        ref_crop = crop(reference, reference_bounds, args.padding)
        render_crop = crop(render, render_bounds, args.padding)
        width = max(ref_crop.width, render_crop.width)
        height = max(ref_crop.height, render_crop.height)
        label_height = 24
        canvas = Image.new("RGB", (width * 2 + 16, height + label_height), (12, 18, 30))
        canvas.paste(ref_crop, (0, label_height))
        canvas.paste(render_crop, (width + 16, label_height))
        draw = ImageDraw.Draw(canvas)
        draw.text((4, 4), "reference", fill=(235, 245, 255))
        draw.text((width + 20, 4), "render", fill=(235, 245, 255))
        output_path = out / f"focus-{index:02d}-{safe_name(metric.get('id'))}.png"
        canvas.save(output_path)
        records.append({
            "rank": index,
            "regionId": metric.get("id"),
            "localDiffDensity": metric.get("localDiffDensity"),
            "anchorError": metric.get("anchorError"),
            "referenceBbox": metric.get("referenceBbox"),
            "renderBbox": metric.get("renderBbox"),
            "path": str(output_path)
        })
        rows.append(canvas)

    if rows:
        row_width = max(image.width for image in rows)
        row_height = max(image.height for image in rows)
        sheet = Image.new("RGB", (row_width, row_height * len(rows)), (12, 18, 30))
        for index, image in enumerate(rows):
            sheet.paste(image, (0, index * row_height))
        sheet.save(out / "focus-sheet.png")

    index_path = out / "focus-index.json"
    index_path.write_text(json.dumps({"status": "pass", "regions": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "report": str(index_path), "count": len(records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
