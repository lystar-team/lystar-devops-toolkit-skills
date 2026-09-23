#!/usr/bin/env python3
"""Scan visual assets with metadata and incremental file caching."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from restore_core import ContractError, atomic_write_json, load_json, sha256_file

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
SVG_EXTENSIONS = {".svg"}
FONT_EXTENSIONS = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
DEFAULT_ROOTS = ["src/assets", "src/static", "public", "static", "assets"]
DEFAULT_EXCLUDES = {".git", ".codegraph", "node_modules", "dist", "build", ".nuxt", ".next", "coverage", ".cache"}


def safe_id(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return result or "asset"


def ratio(width: float | None, height: float | None) -> float | None:
    return width / height if width and height else None


def rgb_stats(colors: list[tuple[int, int, int]]) -> dict[str, Any]:
    if not colors:
        return {"count": 0}
    mean = tuple(sum(color[index] for color in colors) / len(colors) for index in range(3))
    variance = sum(sum((color[index] - mean[index]) ** 2 for index in range(3)) for color in colors) / len(colors)
    return {"count": len(colors), "meanRgb": [round(value, 3) for value in mean], "variance": round(variance, 3)}


def image_metadata(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required by asset_catalog.py") from exc

    with Image.open(path) as image:
        bands = image.getbands()
        result: dict[str, Any] = {
            "naturalSize": {"width": image.width, "height": image.height},
            "aspectRatio": ratio(image.width, image.height),
            "mode": image.mode,
            "hasAlpha": "A" in bands,
            "animated": int(getattr(image, "n_frames", 1)) > 1,
            "frameCount": int(getattr(image, "n_frames", 1)),
        }
        if "A" in bands:
            alpha = image.getchannel("A")
            histogram = alpha.histogram()
            pixel_count = image.width * image.height
            result["alphaNonzeroRatio"] = round(sum(histogram[1:]) / pixel_count, 6) if pixel_count else 0
            result["alphaBoundingBox"] = list(alpha.getbbox()) if alpha.getbbox() else None
        else:
            result["alphaNonzeroRatio"] = None
            result["alphaBoundingBox"] = None

        sample_size = (min(64, image.width), min(64, image.height))
        sample = image.convert("RGB").resize(sample_size)
        sample_pixels = list(sample.get_flattened_data())
        result["sample"] = rgb_stats(sample_pixels)
        if sample.width >= 2 and sample.height >= 2:
            edge: list[tuple[int, int, int]] = []
            center: list[tuple[int, int, int]] = []
            for x in range(sample.width):
                edge.append(sample.getpixel((x, 0)))
                edge.append(sample.getpixel((x, sample.height - 1)))
            for y in range(1, sample.height - 1):
                edge.append(sample.getpixel((0, y)))
                edge.append(sample.getpixel((sample.width - 1, y)))
            center_left = sample.width // 4
            center_top = sample.height // 4
            center_right = max(center_left + 1, sample.width - center_left)
            center_bottom = max(center_top + 1, sample.height - center_top)
            for y in range(center_top, center_bottom):
                for x in range(center_left, center_right):
                    center.append(sample.getpixel((x, y)))
            edge_stats = rgb_stats(edge)
            center_stats = rgb_stats(center)
            result["edge"] = edge_stats
            result["center"] = center_stats
            edge_mean = edge_stats.get("meanRgb", [0, 0, 0])
            center_mean = center_stats.get("meanRgb", [0, 0, 0])
            result["possibleFrame"] = bool(edge_stats.get("variance", 0) < 80 and sum(abs(edge_mean[index] - center_mean[index]) for index in range(3)) > 36)
        return result


def svg_size(text: str) -> dict[str, float] | None:
    view_box = re.search(r"\bviewBox\s*=\s*[\"']\s*([\d.+-]+)[,\s]+([\d.+-]+)[,\s]+([\d.+-]+)[,\s]+([\d.+-]+)\s*[\"']", text, re.IGNORECASE)
    if view_box:
        width = float(view_box.group(3))
        height = float(view_box.group(4))
        if width > 0 and height > 0:
            return {"width": width, "height": height}
    width_match = re.search(r"\bwidth\s*=\s*[\"']\s*([\d.]+)", text, re.IGNORECASE)
    height_match = re.search(r"\bheight\s*=\s*[\"']\s*([\d.]+)", text, re.IGNORECASE)
    if width_match and height_match:
        width = float(width_match.group(1))
        height = float(height_match.group(1))
        if width > 0 and height > 0:
            return {"width": width, "height": height}
    return None


def svg_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    size = svg_size(text)
    return {
        "naturalSize": size,
        "aspectRatio": ratio(size.get("width"), size.get("height")) if size else None,
        "hasAlpha": None,
        "mode": "SVG",
        "validRoot": bool(re.search(r"<svg(?:\s|>)", text, re.IGNORECASE)),
        "hasViewBox": bool(re.search(r"\bviewBox\s*=", text, re.IGNORECASE)),
        "hasText": bool(re.search(r"<text(?:\s|>)", text, re.IGNORECASE)),
        "hasEmbeddedImage": bool(re.search(r"<(?:image|foreignObject)(?:\s|>)", text, re.IGNORECASE)),
        "hasFilter": bool(re.search(r"<filter(?:\s|>)", text, re.IGNORECASE)),
        "byteLength": len(text.encode("utf-8")),
    }


def iter_files(root: Path, scan_paths: list[str], excludes: set[str]) -> Iterable[Path]:
    seen: set[Path] = set()
    for raw in scan_paths:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if not target.exists():
            continue
        candidates = [target] if target.is_file() else target.rglob("*")
        for path in candidates:
            if not path.is_file() or any(part in excludes for part in path.parts):
                continue
            if path.suffix.lower() not in IMAGE_EXTENSIONS | SVG_EXTENSIONS | FONT_EXTENSIONS:
                continue
            if path not in seen:
                seen.add(path)
                yield path


def asset_draft(files: list[dict[str, Any]], scenario_id: str, existing: dict[str, dict[str, Any]] | None = None, previous_catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    existing = existing or {}
    previous_catalog = previous_catalog or {}
    assets: list[dict[str, Any]] = []
    for item in files:
        kind = item["type"]
        if kind == "font":
            continue
        asset_type = "svg" if kind == "svg" else "image"
        metadata = item.get("metadata", {})
        digest = str(item.get("sha256", ""))[:10]
        source_path = item["path"]
        draft = {
            "assetId": f"{safe_id(source_path)}-{digest}" if digest else safe_id(source_path),
            "regionId": "unassigned",
            "evidenceIds": [],
            "type": asset_type,
            "renderMode": "dom-css-svg" if kind == "svg" else "dom-css",
            "visualOwner": "unknown",
            "sourceKind": "project",
            "sourcePath": source_path,
            "generatedPath": None,
            "placeholderPath": None,
            "naturalSize": metadata.get("naturalSize"),
            "displaySize": None,
            "crop": None,
            "geometry": {
                "intrinsicAspectRatio": metadata.get("aspectRatio"),
                "displayAspectRatio": None,
                "aspectTolerance": 0.02,
                "aspectChecked": False,
                "fit": "unknown",
                "visibleBbox": None,
            },
            "background": {"mode": "unknown", "edgeChecked": False, "unintendedFrame": False},
            "transparent": metadata.get("hasAlpha"),
            "state": "unavailable",
            "reviewed": False,
        }
        previous = existing.get(source_path)
        if isinstance(previous, dict):
            merged = dict(draft)
            for key in ("assetId", "regionId", "evidenceIds", "visualOwner", "sourceKind", "generatedPath", "placeholderPath", "displaySize", "crop", "geometry", "background", "generation", "checks", "state", "reviewed"):
                if key in previous:
                    merged[key] = previous[key]
            old_catalog_item = previous_catalog.get(source_path, {})
            unchanged = old_catalog_item.get("sha256") == item.get("sha256")
            if not unchanged:
                merged["state"] = "unavailable"
                merged["reviewed"] = False
                geometry = dict(merged.get("geometry") or {})
                geometry["intrinsicAspectRatio"] = metadata.get("aspectRatio")
                geometry["aspectChecked"] = False
                merged["geometry"] = geometry
                background = dict(merged.get("background") or {})
                background["edgeChecked"] = False
                background["unintendedFrame"] = False
                merged["background"] = background
            draft = merged
        assets.append(draft)
    return {"schemaVersion": "1.0", "scenarioId": scenario_id, "assets": assets}


def load_cache(path: Path, enabled: bool) -> dict[str, dict[str, Any]]:
    if not enabled or not path.is_file():
        return {}
    try:
        document = load_json(path, "asset-catalog.schema.json")
    except ContractError:
        return {}
    return {item["path"]: item for item in document.get("files", []) if isinstance(item, dict) and isinstance(item.get("path"), str)}


def load_existing_assets(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    document = load_json(path, "assets.schema.json")
    result: dict[str, dict[str, Any]] = {}
    for item in document.get("assets", []):
        if not isinstance(item, dict) or not isinstance(item.get("sourcePath"), str):
            continue
        result[item["sourcePath"]] = item
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan project images, SVGs and fonts.")
    parser.add_argument("--root", required=True, help="Project root")
    parser.add_argument("--adapter", help="adapter.json; uses assetRoots when --path is absent")
    parser.add_argument("--path", action="append", help="Relative or absolute asset directory/file; repeatable")
    parser.add_argument("--exclude", action="append", help="Directory name to skip; repeatable")
    parser.add_argument("--out", required=True, help="catalog.json")
    parser.add_argument("--assets-out", help="Optional draft assets.json")
    parser.add_argument("--scenario-id", default="asset-catalog-draft")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    adapter: dict[str, Any] = {}
    if args.adapter:
        try:
            adapter = load_json(Path(args.adapter).expanduser().resolve(), "adapter.schema.json")
        except ContractError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
            return 2
    scan_paths = list(args.path or adapter.get("assetRoots") or DEFAULT_ROOTS)
    excludes = DEFAULT_EXCLUDES | {item for item in (args.exclude or []) if item}
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = root / out
    out = out.resolve()
    assets_path = None
    if args.assets_out:
        assets_path = Path(args.assets_out).expanduser()
        if not assets_path.is_absolute():
            assets_path = root / assets_path
        assets_path = assets_path.resolve()
    previous_catalog = load_cache(out, True)
    cache = {} if args.no_cache else previous_catalog
    try:
        existing_assets = load_existing_assets(assets_path)
    except ContractError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    missing_paths = []
    for raw in scan_paths:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = root / target
        if not target.exists():
            missing_paths.append(raw)

    files: list[dict[str, Any]] = []
    reused_count = 0
    for path in iter_files(root, scan_paths, excludes):
        relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        stat = path.stat()
        cached = cache.get(relative)
        if cached and cached.get("bytes") == stat.st_size and cached.get("mtimeNs") == stat.st_mtime_ns:
            files.append(dict(cached))
            reused_count += 1
            continue
        suffix = path.suffix.lower()
        kind = "font" if suffix in FONT_EXTENSIONS else "svg" if suffix in SVG_EXTENSIONS else "image"
        record: dict[str, Any] = {
            "path": relative,
            "type": kind,
            "bytes": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "sha256": sha256_file(path),
        }
        try:
            if kind == "image":
                record["metadata"] = image_metadata(path)
            elif kind == "svg":
                record["metadata"] = svg_metadata(path)
            else:
                record["metadata"] = {"format": suffix.removeprefix("."), "byteLength": stat.st_size}
        except (OSError, RuntimeError, ValueError) as exc:
            record["status"] = "error"
            record["error"] = str(exc)
        files.append(record)

    duplicate_groups: dict[str, list[str]] = {}
    for item in files:
        digest = item.get("sha256")
        if isinstance(digest, str):
            duplicate_groups.setdefault(digest, []).append(item["path"])
    duplicates = [{"sha256": digest, "paths": paths} for digest, paths in duplicate_groups.items() if len(paths) > 1]
    summary = {
        "fileCount": len(files),
        "imageCount": sum(item["type"] == "image" for item in files),
        "svgCount": sum(item["type"] == "svg" for item in files),
        "fontCount": sum(item["type"] == "font" for item in files),
        "duplicateGroupCount": len(duplicates),
        "missingScanPaths": missing_paths,
        "cacheReusedCount": reused_count,
    }
    catalog = {
        "schemaVersion": "1.0",
        "root": str(root),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scanPaths": scan_paths,
        "excludedDirectories": sorted(excludes),
        "summary": summary,
        "duplicates": duplicates,
        "files": files,
    }
    try:
        atomic_write_json(out, catalog, "asset-catalog.schema.json")
        if assets_path:
            atomic_write_json(assets_path, asset_draft(files, args.scenario_id, existing_assets, previous_catalog), "assets.schema.json")
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2

    status = "unresolved" if not files or missing_paths else "pass"
    print(json.dumps({"status": status, "catalog": str(out), "assetsDraft": str(assets_path) if assets_path else None, "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
