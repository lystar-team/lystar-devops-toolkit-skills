#!/usr/bin/env python3
"""批量验证 designengineer.tools 目录中的公开站点可发现性和还原能力。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright

from extract_rendered_styles import EXTRACT_JS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-url", default="https://designengineer.tools/", help="站点目录地址")
    parser.add_argument("--out", required=True, help="JSON 报告输出路径")
    parser.add_argument("--concurrency", type=int, default=6, help="并发页面数")
    parser.add_argument("--timeout-ms", type=int, default=15000, help="单页面导航超时")
    parser.add_argument("--wait-ms", type=int, default=800, help="DOM 加载后额外等待时间")
    parser.add_argument("--screenshots-dir", help="可选：保存每个站点的首屏截图目录")
    parser.add_argument("--executable-path", default="/usr/bin/google-chrome", help="Chromium/Chrome 可执行文件路径")
    return parser.parse_args()


def load_catalog(url: str) -> list[dict[str, str]]:
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "lystar-web-restore-catalog-test/1.0"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for category_node in soup.find_all("div", class_=re.compile(r"grid_category")):
        heading = category_node.find("h2")
        category = " ".join(heading.get_text(" ", strip=True).split()) if heading else "Unknown"
        for anchor in category_node.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href.startswith(("http://", "https://")) or href in seen:
                continue
            seen.add(href)
            name = " ".join(anchor.get_text(" ", strip=True).split()) or href
            entries.append({"category": category, "name": name, "url": href})
    return entries


DISCOVERY_JS = r"""
() => {
  const styleSheets = [...document.styleSheets];
  let readableStyleSheets = 0;
  for (const sheet of styleSheets) {
    try { void sheet.cssRules.length; readableStyleSheets += 1; } catch (_) {}
  }
  const canvases = [...document.querySelectorAll('canvas')];
  const webgl = canvases.some((canvas) => {
    try { return Boolean(canvas.getContext('webgl') || canvas.getContext('webgl2') || canvas.getContext('experimental-webgl')); }
    catch (_) { return false; }
  });
  const root = document.documentElement;
  const controls = [...document.querySelectorAll('button, [role="button"], [data-theme-toggle]')]
    .filter((element) => /dark|light|system|theme/i.test(`${element.innerText || ''} ${element.getAttribute('aria-label') || ''} ${element.getAttribute('data-theme') || ''}`))
    .slice(0, 20)
    .map((element) => ({
      text: (element.innerText || '').trim().slice(0, 80),
      ariaLabel: element.getAttribute('aria-label') || '',
      theme: element.getAttribute('data-theme') || '',
    }));
  const hasThemeAttribute = Boolean(root.getAttribute('data-theme') || root.getAttribute('data-mode') || root.classList.contains('dark') || root.classList.contains('light'));
  const hasThemeMedia = styleSheets.some((sheet) => {
    try { return [...sheet.cssRules].some((rule) => /prefers-color-scheme/i.test(rule.cssText || '')); }
    catch (_) { return false; }
  });
  let renderMode = 'dom-css';
  if (webgl) renderMode = 'webgl';
  else if (canvases.length) renderMode = 'canvas';
  else if (document.querySelector('svg')) renderMode = 'dom-css-svg';
  else if (document.querySelector('iframe')) renderMode = 'iframe';
  if ((canvases.length || document.querySelector('iframe')) && document.body?.children.length && renderMode !== 'dom-css-svg') renderMode = 'hybrid';
  const bodyText = (document.body?.innerText || '').trim();
  const challengeSample = `${document.title} ${bodyText.slice(0, 800)}`;
  const blockedMarkers = /attention required|just a moment|请稍候|verify you are human|access denied|unable to access|security checkpoint|安全验证|安全检查|异常流量/i;
  return {
    title: document.title,
    readyState: document.readyState,
    finalUrl: location.href,
    textLength: bodyText.length,
    textSample: bodyText.slice(0, 800),
    blocked: blockedMarkers.test(challengeSample),
    elementCount: document.querySelectorAll('*').length,
    styleSheetCount: styleSheets.length,
    readableStyleSheets,
    scriptCount: document.scripts.length,
    externalScriptCount: [...document.scripts].filter((script) => Boolean(script.src)).length,
    iframeCount: document.querySelectorAll('iframe').length,
    iframes: [...document.querySelectorAll('iframe')].slice(0, 10).map((frame) => frame.src || frame.getAttribute('src') || ''),
    svgCount: document.querySelectorAll('svg').length,
    canvasCount: canvases.length,
    webgl,
    renderMode,
    hasThemeAttribute,
    hasThemeMedia,
    themeControls: controls,
    bodyWidth: document.body?.scrollWidth || 0,
    bodyHeight: document.body?.scrollHeight || 0,
  };
}
"""


def classify(status: int | None, data: dict[str, Any] | None, error: str | None) -> str:
    if not data or (status is not None and status >= 500):
        return "unavailable"
    if data.get("blocked") or (status is not None and status >= 400 and data.get("textLength", 0) < 500):
        return "blocked"
    mode = data.get("renderMode")
    if mode in {"canvas", "webgl", "hybrid", "iframe"}:
        return "runtime-hybrid"
    if data.get("readableStyleSheets", 0) > 0 and data.get("elementCount", 0) > 0:
        return "full-dom-css"
    if data.get("elementCount", 0) > 0:
        return "partial-dom"
    return "unavailable"


async def inspect_page(
    browser: Browser,
    entry: dict[str, str],
    semaphore: asyncio.Semaphore,
    timeout_ms: int,
    wait_ms: int,
    screenshot_dir: Path | None,
    index: int,
) -> dict[str, Any]:
    async with semaphore:
        context = await browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        page: Page = await context.new_page()
        response_status: int | None = None
        error: str | None = None
        data: dict[str, Any] | None = None
        extraction: dict[str, Any] | None = None
        extraction_error: str | None = None
        try:
            response = await page.goto(entry["url"], wait_until="commit", timeout=timeout_ms)
            response_status = response.status if response else None
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except Exception as exc:  # 页面已提交但部分站点长期不结束加载
                error = f"domcontentloaded timeout: {exc}"
            if wait_ms:
                await page.wait_for_timeout(wait_ms)
            data = await page.evaluate(DISCOVERY_JS)
            try:
                raw_extraction = await page.evaluate(EXTRACT_JS, {
                    "selector": "body",
                    "maxElements": 300,
                    "fullComputed": False,
                    "includeHtml": False,
                    "theme": "current",
                })
                extraction = {
                    "renderMode": raw_extraction.get("renderMode"),
                    "classTokenCount": raw_extraction.get("coverage", {}).get("classTokenCount", 0),
                    "matchedRuleCount": raw_extraction.get("coverage", {}).get("matchedRuleCount", 0),
                    "unmatchedTokenCount": len(raw_extraction.get("coverage", {}).get("unmatchedTokens", [])),
                    "cssRuleCount": len(raw_extraction.get("cssRules", [])),
                    "assetCount": len(raw_extraction.get("assets", [])),
                    "fontCount": len(raw_extraction.get("fonts", [])),
                    "keyframeCount": len(raw_extraction.get("keyframes", [])),
                    "elementCount": len(raw_extraction.get("elements", [])),
                }
            except Exception as exc:  # noqa: BLE001 - 单站点提取失败要进入报告
                extraction_error = str(exc)
            if screenshot_dir:
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                safe_name = f"{index:03d}-{re.sub(r'[^a-zA-Z0-9._-]+', '-', entry['name']).strip('-')[:50] or 'site'}.png"
                await page.screenshot(path=str(screenshot_dir / safe_name), full_page=False)
        except Exception as exc:  # noqa: BLE001 - 目录测试必须收集单站点失败
            error = str(exc)
            try:
                if page.url not in {"", "about:blank"}:
                    data = await page.evaluate(DISCOVERY_JS)
                    try:
                        raw_extraction = await page.evaluate(EXTRACT_JS, {
                            "selector": "body",
                            "maxElements": 300,
                            "fullComputed": False,
                            "includeHtml": False,
                            "theme": "current",
                        })
                        extraction = {
                            "renderMode": raw_extraction.get("renderMode"),
                            "classTokenCount": raw_extraction.get("coverage", {}).get("classTokenCount", 0),
                            "matchedRuleCount": raw_extraction.get("coverage", {}).get("matchedRuleCount", 0),
                            "unmatchedTokenCount": len(raw_extraction.get("coverage", {}).get("unmatchedTokens", [])),
                            "cssRuleCount": len(raw_extraction.get("cssRules", [])),
                            "assetCount": len(raw_extraction.get("assets", [])),
                            "fontCount": len(raw_extraction.get("fonts", [])),
                            "keyframeCount": len(raw_extraction.get("keyframes", [])),
                            "elementCount": len(raw_extraction.get("elements", [])),
                        }
                    except Exception as extraction_exc:
                        extraction_error = str(extraction_exc)
            except Exception:
                pass
        finally:
            await context.close()
        return {
            **entry,
            "status": response_status,
            "error": error,
            "result": classify(response_status, data, error),
            "discovery": data or {},
            "extractor": extraction,
            "extractorError": extraction_error,
        }


async def run(entries: list[dict[str, str]], args: argparse.Namespace) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    screenshot_dir = Path(args.screenshots_dir) if args.screenshots_dir else None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path=args.executable_path,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            tasks = [
                inspect_page(browser, entry, semaphore, args.timeout_ms, args.wait_ms, screenshot_dir, index)
                for index, entry in enumerate(entries, start=1)
            ]
            results = await asyncio.gather(*tasks)
        finally:
            await browser.close()
    summary: dict[str, int] = {}
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        summary[item["result"]] = summary.get(item["result"], 0) + 1
        category = item["category"]
        category_summary = by_category.setdefault(category, {})
        category_summary[item["result"]] = category_summary.get(item["result"], 0) + 1
    return {
        "schemaVersion": "1.0",
        "testedAt": datetime.now(timezone.utc).isoformat(),
        "catalogUrl": args.catalog_url,
        "total": len(results),
        "summary": summary,
        "byCategory": by_category,
        "results": results,
    }


def main() -> int:
    args = parse_args()
    entries = load_catalog(args.catalog_url)
    report = asyncio.run(run(entries, args))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(output),
        "total": report["total"],
        "summary": report["summary"],
        "categories": len(report["byCategory"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
