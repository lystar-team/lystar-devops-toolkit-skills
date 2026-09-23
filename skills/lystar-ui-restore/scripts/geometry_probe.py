#!/usr/bin/env python3
"""Probe runtime geometry and computed styles for restore regions."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
RENDER_SCRIPT = SCRIPT_DIR / "render_page.py"

from render_page import (  # noqa: E402
    eval_browser,
    load_json,
    parse_last_json,
    run_browser,
    run_command,
    shutil_which,
)
from restore_core import ContractError, atomic_write_json, load_json as load_contract_json, require_same_scenario, stable_hash, validate_document


def emit(status: str, message: str, code: int, **extra: Any) -> int:
    print(json.dumps({"status": status, "message": message, **extra}, ensure_ascii=False))
    return code


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value, "geometry.schema.json")


def selector_map(regions_path: Path | None, explicit: list[str]) -> tuple[dict[str, str], set[str]]:
    selectors: dict[str, str] = {}
    required: set[str] = set()
    if regions_path:
        document = load_json(regions_path)
        regions = document.get("regions", [])
        if not isinstance(regions, list):
            raise ValueError("regions.regions must be an array")
        for item in regions:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            region_id = str(item["id"])
            locator = item.get("locator")
            selector = None
            if isinstance(locator, dict):
                if isinstance(locator.get("selector"), str):
                    selector = locator["selector"]
                elif isinstance(locator.get("css"), str):
                    selector = locator["css"]
                elif isinstance(locator.get("selectors"), list):
                    selector = next((value for value in locator["selectors"] if isinstance(value, str)), None)
            selectors[region_id] = selector or f'[data-restore-region="{region_id}"]'
    for raw in explicit:
        region_id, separator, selector = raw.partition("=")
        if not separator or not region_id.strip() or not selector.strip():
            raise ValueError(f"--selector must use id=css-selector: {raw}")
        key = region_id.strip()
        selectors[key] = selector.strip()
        required.add(key)
    return selectors, required


def probe_script(selectors: dict[str, str]) -> str:
    return """(() => {
  const selectorMap = %s;
  const round = (value) => Number.isFinite(value) ? Math.round(value * 1000) / 1000 : null;
  const rectValue = (rect) => ({
    x: round(rect.x), y: round(rect.y), width: round(rect.width), height: round(rect.height),
    right: round(rect.right), bottom: round(rect.bottom)
  });
  const visible = (element, rect, style) => Boolean(
    rect.width > 0 && rect.height > 0 &&
    style.display !== 'none' && style.visibility !== 'hidden' &&
    style.opacity !== '0'
  );
  const customProperties = (style) => {
    const result = {};
    for (let index = 0; index < style.length; index += 1) {
      const name = style[index];
      if (name && name.startsWith('--')) result[name] = style.getPropertyValue(name).trim();
    }
    return result;
  };
  const inspect = (element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const text = (element.innerText || element.textContent || '').trim();
    return {
      tagName: element.tagName.toLowerCase(),
      className: typeof element.className === 'string' ? element.className : '',
      bbox: rectValue(rect),
      center: {x: round(rect.x + rect.width / 2), y: round(rect.y + rect.height / 2)},
      visible: visible(element, rect, style),
      textLength: text.length,
      attributes: {
        id: element.id || null,
        role: element.getAttribute('role'),
        ariaLabel: element.getAttribute('aria-label'),
        testId: element.getAttribute('data-testid'),
        restoreRegion: element.getAttribute('data-restore-region'),
        tabIndex: element.getAttribute('tabindex')
      },
      style: {
        display: style.display,
        position: style.position,
        zIndex: style.zIndex,
        opacity: style.opacity,
        transform: style.transform,
        transformOrigin: style.transformOrigin,
        overflow: style.overflow,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
        borderRadius: style.borderRadius,
        border: style.border,
        boxShadow: style.boxShadow,
        backgroundColor: style.backgroundColor,
        backgroundImage: style.backgroundImage,
        color: style.color,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        lineHeight: style.lineHeight,
        letterSpacing: style.letterSpacing,
        whiteSpace: style.whiteSpace
      },
      customProperties: customProperties(style),
      scroll: {
        scrollWidth: element.scrollWidth,
        scrollHeight: element.scrollHeight,
        clientWidth: element.clientWidth,
        clientHeight: element.clientHeight
      },
      children: {
        canvas: element.querySelectorAll('canvas').length,
        images: element.querySelectorAll('img').length,
        svg: element.querySelectorAll('svg').length,
        buttons: element.querySelectorAll('button,[role="button"]').length,
        inputs: element.querySelectorAll('input,select,textarea').length
      }
    };
  };
  const inspectSelector = (id, selector) => {
    try {
      const elements = Array.from(document.querySelectorAll(selector)).slice(0, 50);
      return {
        id, selector, matched: elements.length > 0, matchCount: document.querySelectorAll(selector).length,
        matches: elements.map(inspect)
      };
    } catch (error) {
      return {id, selector, matched: false, matchCount: 0, matches: [], error: String(error)};
    }
  };
  const regions = {};
  for (const [id, selector] of Object.entries(selectorMap)) regions[id] = inspectSelector(id, selector);
  const discovered = Array.from(document.querySelectorAll('[data-restore-region]')).map((element) => ({
    id: element.getAttribute('data-restore-region'),
    selector: `[data-restore-region="${element.getAttribute('data-restore-region')}"]`,
    ...inspect(element)
  }));
  const body = document.body;
  const visibleElementCount = body ? Array.from(body.querySelectorAll('*')).filter((element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return visible(element, rect, style);
  }).length : 0;
  return JSON.stringify({
    url: location.href,
    title: document.title || '',
    coordinateSystem: 'css-viewport-px',
    viewport: {
      width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio,
      scrollX: window.scrollX, scrollY: window.scrollY
    },
    document: {
      fonts: document.fonts ? document.fonts.status : 'unavailable',
      imagesComplete: Array.from(document.images).every((image) => image.complete),
      imageCount: document.images.length,
      canvasCount: document.querySelectorAll('canvas').length,
      svgCount: document.querySelectorAll('svg').length,
      visibleElementCount,
      bodyTextLength: body ? (body.innerText || '').trim().length : 0
    },
    regions,
    discovered
  });
})()""" % json.dumps(selectors, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe UI region geometry and computed styles.")
    parser.add_argument("--adapter", required=True, help="adapter.json")
    parser.add_argument("--config", required=True, help="config.json")
    parser.add_argument("--fixture", required=True, help="fixture.json")
    parser.add_argument("--out", required=True, help="geometry.json or output directory")
    parser.add_argument("--regions", help="regions.json; uses locator.selector or data-restore-region")
    parser.add_argument("--capture", help="capture.json from the same render session")
    parser.add_argument("--selector", action="append", default=[], help="Extra selector in id=css-selector form")
    parser.add_argument("--url", help="Override page URL")
    parser.add_argument("--session", help="Probe an existing agent-browser session")
    parser.add_argument("--require-match", action="store_true", help="Return code 3 when a requested selector has no match")
    args = parser.parse_args()

    if not shutil_which("agent-browser"):
        return emit("blocked", "agent-browser is not available in PATH", 3)

    try:
        adapter_path = Path(args.adapter).expanduser().resolve()
        config_path = Path(args.config).expanduser().resolve()
        fixture_path = Path(args.fixture).expanduser().resolve()
        adapter = load_json(adapter_path)
        config = load_json(config_path)
        fixture = load_json(fixture_path)
        validate_document(adapter, "adapter.schema.json", str(adapter_path))
        validate_document(config, "config.schema.json", str(config_path))
        validate_document(fixture, "fixture.schema.json", str(fixture_path))
        regions_path = Path(args.regions).expanduser().resolve() if args.regions else None
        regions_document = load_json(regions_path) if regions_path else None
        if regions_document is not None:
            validate_document(regions_document, "regions.schema.json", str(regions_path))
        capture_path = Path(args.capture).expanduser().resolve() if args.capture else None
        capture_document = load_contract_json(capture_path, "capture.schema.json") if capture_path else None
        require_same_scenario([("config", config), ("fixture", fixture), ("regions", regions_document), ("capture", capture_document)])
        selectors, required_selectors = selector_map(regions_path, args.selector)
    except (ContractError, ValueError, OSError) as exc:
        return emit("error", str(exc), 2)

    out = Path(args.out).expanduser()
    if out.suffix.lower() == ".json":
        geometry_path = out.resolve()
        out_dir = geometry_path.parent
    else:
        out_dir = out.resolve()
        geometry_path = out_dir / "geometry.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = args.session
    owned_session = False
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if not session:
            temp_dir = tempfile.TemporaryDirectory(prefix="lystar-ui-restore-geometry-")
            temp_adapter = dict(adapter)
            base_prefix = str(adapter.get("sessionPrefix", "lystar-ui-restore"))
            temp_adapter["sessionPrefix"] = f"{base_prefix}-geometry-{os.getpid()}"
            temp_adapter_path = Path(temp_dir.name) / "adapter.json"
            temp_adapter_path.write_text(json.dumps(temp_adapter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            render_dir = out_dir / "render"
            render_command = [
                sys.executable, str(RENDER_SCRIPT),
                "--adapter", str(temp_adapter_path),
                "--config", str(config_path),
                "--fixture", str(fixture_path),
                "--out", str(render_dir),
                "--keep-session"
            ]
            if args.url:
                render_command.extend(["--url", args.url])
            rendered = run_command(render_command)
            (out_dir / "render.stdout.txt").write_text(rendered.stdout, encoding="utf-8")
            (out_dir / "render.stderr.txt").write_text(rendered.stderr, encoding="utf-8")
            if rendered.returncode != 0:
                return emit("blocked", "render_page.py failed before geometry probe", 3, stdout=str(out_dir / "render.stdout.txt"), stderr=str(out_dir / "render.stderr.txt"))
            render_result = parse_last_json(rendered.stdout)
            session = render_result.get("session") if isinstance(render_result, dict) else None
            if not isinstance(session, str) or not session:
                return emit("blocked", "render_page.py did not return a session", 3)
            owned_session = True

        probe = probe_script(selectors)
        ok, output = eval_browser(session, probe)
        value = parse_last_json(output) if ok else None
        if not isinstance(value, dict):
            return emit("blocked", "agent-browser did not return geometry JSON", 3)

        unmatched = []
        for region_id, item in value.get("regions", {}).items():
            if isinstance(item, dict) and not item.get("matched"):
                unmatched.append(region_id)
        required_unmatched = sorted(region_id for region_id in (set(unmatched) if args.require_match else required_selectors) if region_id in unmatched)
        result = {
            "schemaVersion": "1.0",
            "scenarioId": config.get("scenarioId", fixture.get("scenarioId", "scenario")),
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "session": session,
            "source": {
                "adapter": str(adapter_path),
                "config": str(config_path),
                "fixture": str(fixture_path),
                "regions": str(regions_path) if regions_path else None,
                "capture": str(capture_path) if capture_path else None
            },
            "captureHash": stable_hash(capture_document) if capture_document else None,
            **value,
            "unmatchedSelectors": unmatched,
            "requiredUnmatchedSelectors": required_unmatched,
            "status": "unresolved" if unmatched else "pass"
        }
        write_json(geometry_path, result)
        if args.require_match and required_unmatched:
            return emit("blocked", "required geometry selectors did not match", 3, geometry=str(geometry_path), unmatched=required_unmatched)
        print(json.dumps({"status": result["status"], "geometry": str(geometry_path), "regions": len(result.get("regions", {})), "unmatched": len(unmatched)}, ensure_ascii=False))
        return 0
    finally:
        if owned_session and isinstance(session, str):
            run_browser(session, ["close"])
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
