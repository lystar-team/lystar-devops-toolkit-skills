#!/usr/bin/env python3
"""Capture a page through agent-browser and save schema-validated runtime evidence."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from coordinate_model import build_transform
from restore_core import (
    ContractError,
    atomic_write_json,
    load_json as load_contract_json,
    require_same_scenario,
    resolve_workflow_mode,
    close_number,
    sha256_file,
    urls_match,
    validate_document,
)


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return load_contract_json(path)
    except ContractError as exc:
        fail(str(exc), 2)


def run_command(command: list[str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, input=input_text, text=True, capture_output=True)
    except FileNotFoundError:
        fail(f"Command not found: {command[0]}", 3)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_browser(session: str, args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return run_command(["agent-browser", "--session", session, *args], input_text)


def eval_browser(session: str, script: str) -> tuple[bool, str]:
    result = run_browser(session, ["eval", "--stdin"], script)
    return result.returncode == 0, result.stdout.strip()


def parse_last_json(text: str) -> Any:
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


def image_size(path: Path) -> list[int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return [image.width, image.height]
    except Exception:
        return None


def image_stats(path: Path) -> dict[str, float | int] | None:
    try:
        from PIL import Image

        with Image.open(path).convert("RGB") as image:
            sample = image.resize((64, 64))
            pixels = list(sample.get_flattened_data())
    except Exception:
        return None
    if not pixels:
        return None
    luminance = [(0.2126 * red + 0.7152 * green + 0.0722 * blue) for red, green, blue in pixels]
    mean = sum(luminance) / len(luminance)
    variance = sum((value - mean) ** 2 for value in luminance) / len(luminance)
    return {
        "meanLuminance": round(mean, 3),
        "luminanceVariance": round(variance, 3),
        "whiteRatio": round(sum(value >= 248 for value in luminance) / len(luminance), 5),
        "blackRatio": round(sum(value <= 7 for value in luminance) / len(luminance), 5),
        "sampleWidth": 64,
        "sampleHeight": 64,
    }


def parse_storage_state(raw: Any) -> tuple[str, list[list[str]]] | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError("fixture.loginState must be a string or null")
    storage, separator, payload = raw.partition(":")
    if not separator or storage not in {"localStorage", "sessionStorage"}:
        raise ValueError("fixture.loginState must use localStorage:key=value or sessionStorage:key=value")
    entries: list[list[str]] = []
    for item in payload.split(";"):
        item = item.strip()
        if not item:
            continue
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError("fixture.loginState contains an invalid key=value entry")
        entries.append([key.strip(), value])
    if not entries:
        raise ValueError("fixture.loginState contains no storage entries")
    return storage, entries


def origin_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def apply_storage_state(session: str, storage_state: tuple[str, list[list[str]]] | None) -> None:
    if storage_state is None:
        return
    storage, entries = storage_state
    script = """(() => {
  const storage = %s === 'sessionStorage' ? window.sessionStorage : window.localStorage;
  const entries = %s;
  for (const [key, value] of entries) storage.setItem(key, value);
  return 'state-applied';
})()""" % (json.dumps(storage), json.dumps(entries, ensure_ascii=False))
    ok, output = eval_browser(session, script)
    if not ok or "state-applied" not in output:
        fail("fixture.loginState could not be applied to the page origin", 3)


def adjust_browser_viewport(session: str, viewport: dict[str, Any]) -> dict[str, Any]:
    requested = {"width": int(viewport["width"]), "height": int(viewport["height"]), "deviceScaleFactor": viewport["deviceScaleFactor"]}
    for attempt in range(2):
        result = run_browser(session, ["set", "viewport", str(requested["width"]), str(requested["height"]), str(requested["deviceScaleFactor"])])
        if result.returncode != 0:
            fail(f"Unable to apply browser viewport after navigation: {result.stderr.strip()}", 3)
        run_browser(session, ["wait", "100"])
        ok, output = eval_browser(session, "JSON.stringify({innerWidth: window.innerWidth, innerHeight: window.innerHeight, outerWidth: window.outerWidth, outerHeight: window.outerHeight, devicePixelRatio: window.devicePixelRatio})")
        observed = parse_last_json(output) if ok else None
        if isinstance(observed, dict) and close_number(observed.get("innerWidth"), requested["width"], 0) and close_number(observed.get("innerHeight"), requested["height"], 0) and close_number(observed.get("devicePixelRatio"), requested["deviceScaleFactor"], 0.01):
            return {"status": "matched" if attempt == 0 else "retry-matched", "requested": requested, "observed": observed, "attempts": attempt + 1}
    return {"status": "mismatch", "requested": requested, "observed": observed if isinstance(observed, dict) else None, "attempts": 2}


def collect_runtime_state(
    session: str,
    ready_selector: str,
    login_indicators: list[str],
    blank_indicators: list[str],
    require_fonts: bool,
    require_images: bool,
) -> dict[str, Any] | None:
    script = """(() => {
  const readySelector = %s;
  const loginIndicators = %s;
  const blankIndicators = %s;
  const requireFonts = %s;
  const requireImages = %s;
  const body = document.body;
  const text = body ? (body.innerText || '').trim() : '';
  const visibleElementCount = body ? Array.from(body.querySelectorAll('*')).filter((element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }).length : 0;
  const fontsStatus = document.fonts ? document.fonts.status : 'unavailable';
  const imagesComplete = Array.from(document.images).every((image) => image.complete);
  const loginByUrl = /(?:^|[\\/#?])login(?:[\\/?#]|$)/i.test(location.href);
  const loginBySelector = loginIndicators.some((selector) => {
    try { return Boolean(document.querySelector(selector)); } catch { return false; }
  });
  const blankBySelector = blankIndicators.some((selector) => {
    try { return Boolean(document.querySelector(selector)); } catch { return false; }
  });
  const blankDetected = !body || blankBySelector || (!text && visibleElementCount === 0);
  const loginDetected = loginByUrl || loginBySelector;
  const reasons = [];
  if (loginDetected) reasons.push('login-page-detected');
  if (blankDetected) reasons.push('blank-page-detected');
  if (readySelector) {
    try {
      if (!document.querySelector(readySelector)) reasons.push('ready-selector-not-found');
    } catch {
      reasons.push('ready-selector-invalid');
    }
  }
  if (requireFonts && fontsStatus !== 'loaded') reasons.push('fonts-not-loaded');
  if (requireImages && !imagesComplete) reasons.push('images-not-complete');
  const status = reasons.length ? 'blocked' : 'ready';
  return JSON.stringify({
    status,
    url: location.href,
    title: document.title || '',
    readySelector: readySelector || null,
    readySelectorMatched: readySelector ? !reasons.includes('ready-selector-not-found') && !reasons.includes('ready-selector-invalid') : null,
    fonts: fontsStatus,
    imagesComplete,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    devicePixelRatio: window.devicePixelRatio,
    bodyTextLength: text.length,
    visibleElementCount,
    loginDetected,
    blankDetected,
    reasons
  });
})()""" % (
        json.dumps(ready_selector),
        json.dumps(login_indicators),
        json.dumps(blank_indicators),
        "true" if require_fonts else "false",
        "true" if require_images else "false",
    )
    ok, output = eval_browser(session, script)
    value = parse_last_json(output) if ok else None
    return value if isinstance(value, dict) else None


def crop_image(source: Path, target: Path, clip: dict[str, Any], scale_x: float, scale_y: float) -> None:
    try:
        from PIL import Image
    except ImportError:
        fail("Pillow is required for clip and scroll-segment screenshots", 3)
    with Image.open(source) as image:
        left = max(0, int(round(float(clip.get("x", 0)) * scale_x)))
        top = max(0, int(round(float(clip.get("y", 0)) * scale_y)))
        right = min(image.width, left + max(0, int(round(float(clip.get("width", 0)) * scale_x))))
        bottom = min(image.height, top + max(0, int(round(float(clip.get("height", 0)) * scale_y))))
        if right <= left or bottom <= top:
            fail("Screenshot clip has no visible area", 3)
        image.crop((left, top, right, bottom)).save(target)


def apply_state_actions(session: str, actions: list[Any]) -> None:
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            fail(f"fixture.stateActions[{index}] must be an object", 2)
        kind = str(action.get("action", ""))
        selector = action.get("selector")
        if kind in {"click", "fill", "hover"} and not isinstance(selector, str):
            fail(f"fixture.stateActions[{index}] requires a selector", 2)
        if kind == "click":
            command = ["click", selector]
        elif kind == "fill":
            if not isinstance(action.get("value"), str):
                fail(f"fixture.stateActions[{index}] fill requires a string value", 2)
            command = ["fill", selector, action["value"]]
        elif kind == "hover":
            command = ["hover", selector]
        elif kind == "press":
            if not isinstance(action.get("key"), str):
                fail(f"fixture.stateActions[{index}] press requires a key", 2)
            command = ["press", action["key"]]
        elif kind == "wait":
            wait_value = action.get("ms", action.get("value", 200))
            if not isinstance(wait_value, (int, float, str)):
                fail(f"fixture.stateActions[{index}] wait requires ms", 2)
            command = ["wait", str(wait_value)]
        else:
            fail(f"fixture.stateActions[{index}] uses unsupported action: {kind}", 2)
        result = run_browser(session, command)
        if result.returncode != 0:
            fail(f"fixture.stateActions[{index}] failed: {kind}", 3)


def wait_for_readiness(session: str, conditions: list[Any], timeout_ms: int) -> None:
    for condition in conditions:
        name = str(condition)
        if name == "document.fonts.ready":
            script = """(async () => {
  if (!document.fonts) return 'fonts-unavailable';
  const timeout = new Promise(resolve => setTimeout(() => resolve('fonts-timeout'), %s));
  const ready = document.fonts.ready.then(() => 'fonts-ready');
  return await Promise.race([ready, timeout]);
})()""" % timeout_ms
            ok, output = eval_browser(session, script)
            if not ok or "fonts-ready" not in output:
                fail("document.fonts.ready was not satisfied", 3)
        elif name == "images-complete":
            script = """(async () => {
  const deadline = Date.now() + %s;
  while (Date.now() < deadline) {
    if (Array.from(document.images).every(image => image.complete)) return 'images-ready';
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  return 'images-timeout';
})()""" % timeout_ms
            ok, output = eval_browser(session, script)
            if not ok or "images-ready" not in output:
                fail("images-complete was not satisfied", 3)
        else:
            waited = run_browser(session, ["wait", name.removeprefix("selector:")])
            if waited.returncode != 0:
                fail(f"readiness condition was not satisfied: {name}", 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a UI page with agent-browser.")
    parser.add_argument("--adapter", required=True, help="adapter.json")
    parser.add_argument("--config", required=True, help="scenario config.json")
    parser.add_argument("--fixture", required=True, help="fixture.json")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--url", help="Override page URL")
    parser.add_argument("--session", help="Existing agent-browser session")
    parser.add_argument("--keep-session", action="store_true", help="Keep a session created by this command")
    args = parser.parse_args()

    if not shutil_which("agent-browser"):
        fail("agent-browser is not available in PATH", 3)

    adapter = load_json(Path(args.adapter).expanduser().resolve())
    config = load_json(Path(args.config).expanduser().resolve())
    fixture = load_json(Path(args.fixture).expanduser().resolve())
    try:
        validate_document(adapter, "adapter.schema.json", args.adapter)
        validate_document(config, "config.schema.json", args.config)
        validate_document(fixture, "fixture.schema.json", args.fixture)
        require_same_scenario([("config", config), ("fixture", fixture)])
        workflow_mode = resolve_workflow_mode(config)
    except ContractError as exc:
        fail(str(exc), 2)

    capture_adapter = str(config.get("captureAdapter") or adapter.get("captureAdapter") or "agent-browser")
    if capture_adapter != "agent-browser":
        fail(f"captureAdapter '{capture_adapter}' needs its platform adapter; render_page.py runs agent-browser", 3)
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    viewport = config.get("viewport")
    if not isinstance(viewport, dict) or not all(key in viewport for key in ("width", "height", "deviceScaleFactor")):
        fail("config.viewport must include width, height and deviceScaleFactor", 3)

    configured_runtime = adapter.get("runtime", {})
    readiness_policy = config.get("readiness", {})
    if not isinstance(configured_runtime, dict) or not isinstance(readiness_policy, dict):
        fail("adapter.runtime and config.readiness must be objects", 2)
    for field in ("apiFixture", "freezeTime", "seed"):
        if fixture.get(field) not in (None, ""):
            fail(f"fixture.{field} requires a capture adapter implementation", 3)
    for runtime_key in ("locale", "timezone"):
        requested = fixture.get(runtime_key, config.get(runtime_key))
        if requested is not None and configured_runtime.get(runtime_key) != requested:
            fail(f"Requested {runtime_key} is not configured by adapter.runtime", 3)

    prefix = str(adapter.get("sessionPrefix", "lystar-ui-restore"))
    scope = str(adapter.get("sessionScope", "worktree"))
    created_session = False
    session = args.session
    if not session:
        session_result = run_command(["agent-browser", "session", "id", "--scope", scope, "--prefix", prefix])
        if session_result.returncode != 0 or not session_result.stdout.strip():
            fail(f"Unable to create agent-browser session: {session_result.stderr.strip()}", 3)
        session = session_result.stdout.strip().splitlines()[-1].strip()
        created_session = True

    def close_owned_session() -> None:
        if created_session and not args.keep_session and isinstance(session, str):
            run_browser(session, ["close"])

    if created_session and not args.keep_session:
        atexit.register(close_owned_session)

    viewport_result = run_browser(session, ["set", "viewport", str(viewport["width"]), str(viewport["height"]), str(viewport["deviceScaleFactor"])])
    if viewport_result.returncode != 0:
        fail(f"Unable to apply viewport: {viewport_result.stderr.strip()}", 3)

    network_mode = str(fixture.get("networkMode", "unknown"))
    if network_mode == "fixture":
        fail("fixture network mode requires a capture adapter implementation", 3)
    if network_mode == "offline":
        offline_result = run_browser(session, ["set", "offline", "on"])
        if offline_result.returncode != 0:
            fail(f"Unable to enable offline mode: {offline_result.stderr.strip()}", 3)

    theme = str(config.get("theme", ""))
    if theme in {"dark", "light"}:
        media_args = ["set", "media", theme]
        if fixture.get("disableAnimation", config.get("disableAnimation", True)):
            media_args.append("reduced-motion")
        media_result = run_browser(session, media_args)
        if media_result.returncode != 0:
            fail(f"Unable to set media preferences: {media_result.stderr.strip()}", 3)

    base_url = str(args.url or adapter.get("baseUrl", ""))
    if not args.url:
        entry = str(config.get("route") or fixture.get("route") or adapter.get("entry", ""))
        page_url = urljoin(base_url.rstrip("/") + "/", entry.lstrip("/"))
    else:
        page_url = base_url

    try:
        storage_state = parse_storage_state(fixture.get("loginState"))
    except ValueError as exc:
        fail(str(exc), 2)
    if storage_state is not None and not adapter.get("stateEntry"):
        fail("fixture.loginState requires adapter.stateEntry", 3)

    network_idle = bool(readiness_policy.get("networkIdle", True))
    if storage_state is not None:
        state_entry = str(adapter.get("stateEntry") or origin_url(page_url))
        if state_entry.startswith(("localStorage:", "sessionStorage:")):
            bootstrap_url = origin_url(page_url)
        else:
            bootstrap_url = state_entry if urlsplit(state_entry).scheme else urljoin(base_url.rstrip("/") + "/", state_entry.lstrip("/"))
        bootstrap = run_browser(session, ["--restore", "open", bootstrap_url])
        if bootstrap.returncode != 0:
            fail(f"agent-browser bootstrap open failed: {bootstrap.stderr.strip()}", 4)
        if network_idle:
            bootstrap_wait = run_browser(session, ["wait", "--load", "networkidle"])
            if bootstrap_wait.returncode != 0:
                fail(f"agent-browser bootstrap wait failed: {bootstrap_wait.stderr.strip()}", 4)
        apply_storage_state(session, storage_state)

    opened = run_browser(session, ["--restore", "open", page_url])
    if opened.returncode != 0:
        fail(f"agent-browser open failed: {opened.stderr.strip()}", 4)
    if network_idle:
        waited = run_browser(session, ["wait", "--load", "networkidle"])
        if waited.returncode != 0:
            fail(f"agent-browser wait failed: {waited.stderr.strip()}", 4)
    run_browser(session, ["wait", "200"])

    animation_disabled = bool(fixture.get("disableAnimation", config.get("disableAnimation", True)))
    viewport_adjustment = adjust_browser_viewport(session, viewport)
    if animation_disabled:
        freeze_script = """(() => {
  const id = 'lystar-ui-restore-freeze';
  let style = document.getElementById(id);
  if (!style) {
    style = document.createElement('style');
    style.id = id;
    style.textContent = '*,:before,:after{animation:none!important;transition:none!important;caret-color:transparent!important}';
    document.documentElement.appendChild(style);
  }
  return 'frozen';
})()"""
        ok, output = eval_browser(session, freeze_script)
        if not ok or "frozen" not in output:
            fail("Unable to disable page animation", 3)

    wait_for = fixture.get("waitFor", [])
    if not isinstance(wait_for, list):
        fail("fixture.waitFor must be an array", 2)
    wait_for = list(wait_for)
    require_fonts = bool(readiness_policy.get("requireFonts", True))
    require_images = bool(readiness_policy.get("requireImages", True))
    if require_fonts and "document.fonts.ready" not in wait_for:
        wait_for.append("document.fonts.ready")
    if require_images and "images-complete" not in wait_for:
        wait_for.append("images-complete")
    if wait_for:
        wait_for_readiness(session, wait_for, int(readiness_policy.get("timeoutMs", 5000)))

    state_actions = fixture.get("stateActions", [])
    if not isinstance(state_actions, list):
        fail("fixture.stateActions must be an array", 2)
    if state_actions:
        apply_state_actions(session, state_actions)
        run_browser(session, ["wait", "200"])

    ready_selector = str(config.get("readySelector") or adapter.get("readySelector") or "")
    scroll = config.get("scroll")
    if scroll is not None:
        if not isinstance(scroll, dict):
            fail("config.scroll must be an object", 2)
        scroll_x = float(scroll.get("x", 0))
        scroll_y = float(scroll.get("y", 0))
        scroll_script = """(() => {
  window.scrollTo(%s, %s);
  return JSON.stringify({x: window.scrollX, y: window.scrollY});
})()""" % (scroll_x, scroll_y)
        ok, scroll_output = eval_browser(session, scroll_script)
        scroll_value = parse_last_json(scroll_output) if ok else None
        if not isinstance(scroll_value, dict) or abs(float(scroll_value.get("x", 0)) - scroll_x) > 1 or abs(float(scroll_value.get("y", 0)) - scroll_y) > 1:
            fail("Requested scroll position was not applied", 3)

    login_indicators = adapter.get("loginIndicators", [])
    blank_indicators = adapter.get("blankIndicators", [])
    if not isinstance(login_indicators, list) or not all(isinstance(item, str) for item in login_indicators):
        fail("adapter.loginIndicators must be an array of selectors", 2)
    if not isinstance(blank_indicators, list) or not all(isinstance(item, str) for item in blank_indicators):
        fail("adapter.blankIndicators must be an array of selectors", 2)
    runtime_state = collect_runtime_state(session, ready_selector, login_indicators, blank_indicators, require_fonts, require_images)
    if not isinstance(runtime_state, dict):
        fail("Unable to collect runtime readiness state", 4)
    if runtime_state.get("status") != "ready":
        reasons = ", ".join(str(item) for item in runtime_state.get("reasons", []))
        fail(f"Page is not ready for capture: {reasons or 'unknown readiness failure'}", 3)
    if runtime_state.get("innerWidth") != viewport.get("width") or runtime_state.get("innerHeight") != viewport.get("height"):
        fail(f"Runtime viewport {runtime_state.get('innerWidth')}x{runtime_state.get('innerHeight')} does not match configured viewport {viewport.get('width')}x{viewport.get('height')}", 3)
    if not urls_match(runtime_state.get("url"), page_url):
        fail(f"Runtime URL {runtime_state.get('url')} does not match requested URL {page_url}", 3)
    if not close_number(runtime_state.get("devicePixelRatio"), viewport.get("deviceScaleFactor"), 0.01):
        fail(f"Runtime DPR {runtime_state.get('devicePixelRatio')} does not match configured DPR {viewport.get('deviceScaleFactor')}", 3)

    screenshot_mode = str(config.get("screenshotMode", "viewport"))
    screenshot_path = out / "render.png"
    temp_path: Path | None = None
    if screenshot_mode == "fullPage":
        captured = run_browser(session, ["screenshot", "--full", str(screenshot_path)])
    elif screenshot_mode in {"clip", "scroll-segment"}:
        temp_file = tempfile.NamedTemporaryFile(prefix="lystar-ui-restore-", suffix=".png", delete=False)
        temp_file.close()
        temp_path = Path(temp_file.name)
        captured = run_browser(session, ["screenshot", str(temp_path)])
        if captured.returncode == 0:
            clip = config.get("scroll", {}).get("clip") if isinstance(config.get("scroll"), dict) else None
            if not isinstance(clip, dict):
                fail(f"{screenshot_mode} requires scroll.clip", 3)
            raw_size = image_size(temp_path)
            if not raw_size:
                fail("Unable to read temporary screenshot size", 4)
            crop_image(temp_path, screenshot_path, clip, raw_size[0] / float(viewport["width"]), raw_size[1] / float(viewport["height"]))
    else:
        captured = run_browser(session, ["screenshot", str(screenshot_path)])

    if temp_path and temp_path.exists():
        temp_path.unlink()
    if captured.returncode != 0:
        fail(f"agent-browser screenshot failed: {captured.stderr.strip()}", 4)

    if screenshot_mode != "fullPage":
        actual_size = image_size(screenshot_path)
        expected_width = viewport.get("imageWidth")
        expected_height = viewport.get("imageHeight")
        if expected_width is not None and expected_height is not None and actual_size != [expected_width, expected_height]:
            fail(f"Screenshot size {actual_size} does not match configured image size {[expected_width, expected_height]}", 3)

    diagnostics: dict[str, str] = {}
    for name, command in {
        "console.txt": ["console"],
        "errors.txt": ["errors"],
        "a11y.json": ["a11y", "--json"],
        "network.txt": ["network", "requests"],
        "vitals.json": ["vitals", "--json"],
        "session.json": ["session", "info", "--json"],
    }.items():
        result = run_browser(session, command)
        path = out / name
        write_text(path, result.stdout if result.stdout else result.stderr)
        diagnostics[name] = str(path)

    if readiness_policy.get("failOnPageErrors", True):
        error_lines = []
        for line in (out / "errors.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped == "✗" or stripped.startswith("[agent-browser] restore:"):
                continue
            error_lines.append(stripped)
        if error_lines:
            fail(f"Browser page errors are present: {out / 'errors.txt'}", 3)

    capture_scroll = config.get("scroll", {"x": 0, "y": 0, "clip": None})
    if isinstance(capture_scroll, dict):
        capture_scroll = {key: capture_scroll.get(key) for key in ("x", "y", "clip")}
    capture = {
        "schemaVersion": "1.0",
        "scenarioId": config.get("scenarioId", fixture.get("scenarioId", "scenario")),
        "workflowMode": workflow_mode,
        "referenceSource": config.get("referenceMode", "local-live"),
        "referenceEnvironment": {"knowledge": "unknown"},
        "renderEnvironment": {
            "knowledge": "partially-known",
            "browser": "agent-browser + Chrome/CDP",
            "toolVersion": "agent-browser",
            "runtimeViewport": runtime_state,
            "viewportAdjustment": viewport_adjustment,
        },
        "viewport": config.get("viewport", {}),
        "route": page_url,
        "pageState": fixture.get("pageState", "unknown"),
        "scroll": capture_scroll,
        "theme": config.get("theme", "unknown"),
        "runtimeOverrides": {
            "locale": fixture.get("locale", config.get("locale")),
            "timezone": fixture.get("timezone", config.get("timezone")),
            "adapterRuntime": configured_runtime,
        },
        "runtimeState": {
            "requested": {
                "locale": fixture.get("locale", config.get("locale")),
                "timezone": fixture.get("timezone", config.get("timezone")),
                "networkMode": network_mode,
                "theme": config.get("theme"),
                "disableAnimation": animation_disabled,
                "loginState": storage_state is not None,
            },
            "applied": {
                "adapterRuntime": configured_runtime,
                "networkMode": network_mode if network_mode == "offline" else "adapter-default",
                "theme": config.get("theme") if theme in {"dark", "light"} else "adapter-default",
                "disableAnimation": animation_disabled,
                "loginState": storage_state is not None,
            },
            "observed": runtime_state,
            "unsupported": [],
        },
        "fontSet": {"status": runtime_state.get("fonts", "unknown")},
        "animation": {"disabled": animation_disabled},
        "readiness": {
            "status": runtime_state.get("status", "unknown"),
            "url": runtime_state.get("url", ""),
            "title": runtime_state.get("title", ""),
            "readySelector": runtime_state.get("readySelector"),
            "readySelectorMatched": runtime_state.get("readySelectorMatched"),
            "fonts": runtime_state.get("fonts", "unknown"),
            "imagesComplete": runtime_state.get("imagesComplete"),
            "bodyTextLength": runtime_state.get("bodyTextLength"),
            "visibleElementCount": runtime_state.get("visibleElementCount"),
            "loginDetected": bool(runtime_state.get("loginDetected")),
            "blankDetected": bool(runtime_state.get("blankDetected")),
            "screenshot": str(screenshot_path),
            "imageSize": image_size(screenshot_path),
            "imageSha256": sha256_file(screenshot_path),
            "imageStats": image_stats(screenshot_path),
            "reasons": runtime_state.get("reasons", []),
        },
        "coordinateSystem": "render-image-px with css-viewport evidence",
        "renderMode": config.get("renderMode", "dom-css"),
        "diagnostics": diagnostics,
        "sourceHashes": {"renderImageSha256": sha256_file(screenshot_path)},
        "session": {"id": session, "scope": scope, "prefix": prefix},
    }
    try:
        capture["coordinateTransform"] = build_transform(capture, config)
        atomic_write_json(out / "capture.json", capture, "capture.schema.json")
    except (ContractError, OSError) as exc:
        fail(str(exc), 2)

    if created_session and not args.keep_session:
        close_owned_session()
        atexit.unregister(close_owned_session)

    print(json.dumps({"status": "completed", "session": session, "url": page_url, "screenshot": str(screenshot_path), "capture": str(out / "capture.json")}, ensure_ascii=False))
    return 0


def shutil_which(command: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
