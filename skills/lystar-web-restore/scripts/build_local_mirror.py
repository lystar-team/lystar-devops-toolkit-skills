#!/usr/bin/env python3
"""把公开页面里的目标组件做成本地可打开的 HTML/CSS 镜像。"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import indent
from typing import Any

try:
    from playwright.sync_api import Frame, Page, sync_playwright
except ImportError as exc:  # pragma: no cover - 运行环境提示
    print(
        "缺少 Python Playwright。请先在当前环境安装 Playwright，"
        "或使用 agent-browser eval --stdin 执行页面采集。",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc

try:
    from extract_rendered_styles import (
        apply_state,
        apply_theme,
        apply_viewport,
        capture_state,
        detect_platform,
        find_frame,
        find_page,
        frame_href,
        parse_themes,
        parse_viewports,
        resolve_states,
        resolve_themes,
    )
except ImportError as exc:  # pragma: no cover - 直接复制脚本时的提示
    print("找不到同目录的 extract_rendered_styles.py。", file=sys.stderr)
    raise SystemExit(2) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("AGENT_BROWSER_CDP_URL", "http://127.0.0.1:9222"),
        help="已有浏览器的 CDP HTTP/WS 地址",
    )
    parser.add_argument("--page-url", required=True, help="要连接的页面 URL 子串")
    parser.add_argument("--frame-url", help="优先选择 URL 包含此子串的 frame")
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "generic", "v0", "21st", "runtime"],
        help="来源处理模式；默认自动发现",
    )
    parser.add_argument("--selector", default="body", help="目标组件的 CSS 选择器")
    parser.add_argument(
        "--states",
        default="base,hover,focus",
        help="要记录的状态，逗号分隔：base、hover、focus、active、auto",
    )
    parser.add_argument(
        "--themes",
        default="light,dark",
        help="要采集的主题，逗号分隔：light、dark、current、auto",
    )
    parser.add_argument(
        "--viewports",
        default="current",
        help="视口：current、desktop、tablet、mobile 或 WIDTHxHEIGHT，逗号分隔",
    )
    parser.add_argument("--max-elements", type=int, default=1000)
    parser.add_argument("--wait-ms", type=int, default=0, help="打开页面后额外等待毫秒数")
    parser.add_argument(
        "--all-css",
        action="store_true",
        help="把可读取的页面 CSS 全部写入镜像；默认只写目标组件需要的规则",
    )
    parser.add_argument("--out", required=True, help="镜像输出目录")
    return parser.parse_args()


def capture_args(args: argparse.Namespace, theme: str) -> argparse.Namespace:
    """镜像需要 HTML，但不需要 full computed，避免把诊断数据写进镜像。"""
    return argparse.Namespace(
        selector=args.selector,
        max_elements=args.max_elements,
        full_computed=False,
        include_html=True,
        theme=theme,
    )


def signature(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def merge_class_rules(captures: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    for capture in captures:
        for token, rules in capture.get("classes", {}).items():
            token_rules = merged.setdefault(token, {})
            for rule in rules:
                token_rules[signature(rule)] = rule
    return {
        token: list(rules.values())
        for token, rules in merged.items()
    }


def selector_uses_target_context(
    selector: str,
    attribute_names: set[str],
    ids: set[str],
) -> bool:
    selector = selector.strip()
    if not selector:
        return False

    # 页面重置、根变量、元素默认样式和 body 基础样式会影响镜像，即使它们没有 class。
    if ":root" in selector or re.search(r"(^|[,\s>+~])(html|body)(?=$|[\s>+~:#.\[])", selector):
        return True
    if not re.search(r"[.#\[]", selector):
        return True

    if "[" in selector and any(re.search(rf"\[{re.escape(name)}(?:[=~|^$*\]]|\s)", selector) for name in attribute_names):
        return True
    if "#" in selector and any(f"#{item}" in selector for item in ids):
        return True
    return False


def selected_rules(
    base: dict[str, Any],
    captures: list[dict[str, Any]],
    all_css: bool,
) -> list[dict[str, Any]]:
    class_rules = merge_class_rules(captures)
    wanted = {
        signature(rule)
        for rules in class_rules.values()
        for rule in rules
    }

    elements = base.get("elements", [])
    attribute_names = {
        name
        for element in elements
        for name in element.get("attributes", {})
    }
    ids = {
        value
        for element in elements
        for name, value in element.get("attributes", {}).items()
        if name == "id" and value
    }

    rules_by_signature: dict[str, dict[str, Any]] = {}
    ordered_rules: list[dict[str, Any]] = []
    for capture in captures:
        for rule in capture.get("cssRules", []):
            key = signature(rule)
            if key not in rules_by_signature:
                rules_by_signature[key] = rule
                ordered_rules.append(rule)

    result: list[dict[str, Any]] = []
    for rule in ordered_rules:
        kind = rule.get("kind")
        if kind in {"keyframes", "font-face", "raw"}:
            include = all_css or kind != "raw"
        elif kind == "style":
            include = all_css or signature(rule) in wanted or selector_uses_target_context(
                str(rule.get("selector") or ""), attribute_names, ids
            )
        else:
            include = False
        if include:
            result.append(rule)
    return result


def render_declarations(declarations: dict[str, Any], level: int = 0) -> str:
    lines = [f"{property_name}: {value};" for property_name, value in declarations.items() if value]
    return indent("\n".join(lines), " " * level)


def wrap_conditions(content: str, conditions: list[str] | None) -> str:
    result = content
    for condition in reversed(conditions or []):
        if not condition or condition.startswith("["):
            continue
        result = f"{condition} {{\n{indent(result, '  ')}\n}}"
    return result


def split_selector_list(value: str) -> list[str]:
    """按顶层逗号拆选择器，避免拆坏 :is()、属性值或字符串。"""
    result: list[str] = []
    current: list[str] = []
    round_depth = 0
    square_depth = 0
    quote = ""
    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
        elif char == "(":
            round_depth += 1
            current.append(char)
        elif char == ")":
            round_depth = max(0, round_depth - 1)
            current.append(char)
        elif char == "[":
            square_depth += 1
            current.append(char)
        elif char == "]":
            square_depth = max(0, square_depth - 1)
            current.append(char)
        elif char == "," and round_depth == 0 and square_depth == 0:
            selector = "".join(current).strip()
            if selector:
                result.append(selector)
            current = []
        else:
            current.append(char)
    selector = "".join(current).strip()
    if selector:
        result.append(selector)
    return result


THEME_MEDIA_RE = re.compile(
    r"^@media\s+(.+?)\s*$",
    re.IGNORECASE,
)
THEME_QUERY_RE = re.compile(
    r"^\(\s*prefers-color-scheme\s*:\s*(light|dark)\s*\)$",
    re.IGNORECASE,
)


def theme_media_condition(condition: str) -> tuple[str, str | None] | None:
    """读取包含 prefers-color-scheme 的媒体条件，并保留其他条件。"""
    match = THEME_MEDIA_RE.match(condition.strip())
    if not match:
        return None

    query = match.group(1).strip()
    # 只拆安全的 and 条件；遇到 or 或逗号时不改写，避免改变 CSS 逻辑。
    if re.search(r"\s(?:or|not)\s|,", query, re.IGNORECASE):
        return None
    parts = [part.strip() for part in re.split(r"\s+and\s+", query, flags=re.IGNORECASE)]
    theme: str | None = None
    remaining: list[str] = []
    for part in parts:
        query_match = THEME_QUERY_RE.match(part)
        if not query_match:
            remaining.append(part)
            continue
        current_theme = query_match.group(1).lower()
        if theme and theme != current_theme:
            return None
        theme = current_theme

    if not theme:
        return None
    remaining_condition = " and ".join(item for item in remaining if item)
    return theme, f"@media {remaining_condition}" if remaining_condition else None


def mirror_theme_selector(selector: str, theme: str) -> str:
    """把媒体主题规则改成可由本地 data-mirror-theme 手动切换的选择器。"""
    attribute_selector = f'html[data-mirror-theme="{theme}"]'
    class_pattern = re.compile(
        rf"(?<![A-Za-z0-9_-])(?:html)?\.{re.escape(theme)}(?![A-Za-z0-9_-])"
    )
    mirrored: list[str] = []
    for item in split_selector_list(selector):
        if ":root" in item:
            mirrored.append(re.sub(r":root\b", attribute_selector, item))
        elif class_pattern.search(item):
            # v0 和 21st 常把主题写在 html.dark/html.light；本地脚本也会同步这个 class。
            mirrored.append(item)
        elif re.match(r"^html(?=$|[.#:\[])", item):
            mirrored.append(re.sub(r"^html\b", attribute_selector, item, count=1))
        else:
            mirrored.append(f"{attribute_selector} {item}")
    return ", ".join(mirrored)


def mirror_theme_rule(rule: dict[str, Any], theme: str) -> dict[str, Any] | None:
    """为主题媒体规则生成一个 data-mirror-theme 版本，保留原规则不动。"""
    if rule.get("kind") != "style":
        return None

    conditions = list(rule.get("conditions") or [])
    remaining: list[str] = []
    found = False
    for condition in conditions:
        parsed = theme_media_condition(str(condition))
        if not parsed:
            remaining.append(condition)
            continue
        media_theme, other_condition = parsed
        if media_theme != theme:
            return None
        found = True
        if other_condition:
            remaining.append(other_condition)

    if not found:
        return None
    mirrored = dict(rule)
    mirrored["selector"] = mirror_theme_selector(str(rule.get("selector") or ""), theme)
    mirrored["conditions"] = remaining
    return mirrored


def render_rule(rule: dict[str, Any]) -> str:
    kind = rule.get("kind")
    if kind == "style":
        selector = str(rule.get("selector") or "").strip()
        declarations = render_declarations(rule.get("declarations") or {}, 2)
        if not selector or not declarations:
            return ""
        content = f"{selector} {{\n{declarations}\n}}"
    elif kind == "font-face":
        declarations = render_declarations(rule.get("declarations") or {}, 2)
        if not declarations:
            return ""
        content = f"@font-face {{\n{declarations}\n}}"
    elif kind == "keyframes":
        frames = []
        for frame in rule.get("frames", []):
            declarations = render_declarations(frame.get("declarations") or {}, 4)
            if declarations:
                frames.append(f"  {frame.get('key', 'from')} {{\n{declarations}\n  }}")
        if not frames:
            return ""
        content = f"@keyframes {rule.get('name', '')} {{\n" + "\n".join(frames) + "\n}"
    elif kind == "raw":
        content = str(rule.get("css") or "").strip()
        if not content:
            return ""
    else:
        return ""
    return wrap_conditions(content, rule.get("conditions"))


def render_css(
    rules: list[dict[str, Any]],
    resolved_variables: dict[str, str],
    source_url: str,
    theme_variables: dict[str, dict[str, str]] | None = None,
    style_sheets: list[dict[str, Any]] | None = None,
) -> str:
    parts = [
        "/* Generated local mirror. CSS is read from the public page CSSOM. */",
        f"/* Source: {source_url} */",
    ]

    # 跨域 stylesheet 的 cssRules 可能不可读，但公开 CSS 仍可通过 @import 保留。
    # 这样普通浏览器打开镜像时仍能加载原站的完整样式，不把不可读误判成无样式。
    for sheet in style_sheets or []:
        href = str(sheet.get("href") or "")
        if href and href not in {"[inline]", source_url} and sheet.get("readable") is False:
            escaped = href.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'@import url("{escaped}");')

    if resolved_variables:
        declarations = render_declarations(resolved_variables, 2)
        parts.append(f":root {{\n{declarations}\n}}")

    active_themes = {
        theme for theme in (theme_variables or {}) if theme in {"light", "dark"}
    }

    for rule in rules:
        rendered = render_rule(rule)
        if rendered:
            parts.append(rendered)

    # 保留源站的 @media 规则，同时补一个可由本地 setMirrorTheme() 控制的版本。
    # 这样手动切换主题时，不会只改变变量而漏掉媒体查询里的普通 CSS。
    for theme in ("light", "dark"):
        if theme not in active_themes:
            continue
        for rule in rules:
            mirrored = mirror_theme_rule(rule, theme)
            rendered = render_rule(mirrored) if mirrored else ""
            if rendered:
                parts.append(rendered)

    for theme, variables in (theme_variables or {}).items():
        if theme not in {"light", "dark"} or not variables:
            continue
        declarations = render_declarations(variables, 2)
        parts.append(f'html[data-mirror-theme="{theme}"] {{\n{declarations}\n}}')
    return "\n\n".join(parts) + "\n"


def opening_tag(descriptor: dict[str, Any]) -> str:
    tag = str(descriptor.get("tag") or "div")
    attributes = descriptor.get("attributes") or {}
    rendered = []
    for name, value in attributes.items():
        rendered.append(
            f' {name}="{html_lib.escape(str(value), quote=True)}"'
        )
    return f"<{tag}{''.join(rendered)}>"


def theme_script(theme_names: list[str]) -> str:
    if not {"light", "dark"}.issubset(set(theme_names)):
        return ""
    return """  <script>
    (() => {
      const themeAttributes = ['data-theme', 'data-color-scheme', 'data-color-mode', 'data-mode'];
      const setMirrorTheme = (theme) => {
        const normalized = theme === 'dark' ? 'dark' : 'light';
        const candidates = [
          document.documentElement,
          document.body,
          ...document.querySelectorAll('[data-theme], [data-color-scheme], [data-color-mode], [data-mode]')
        ].filter(Boolean);
        for (const element of [...new Set(candidates)]) {
          const tokens = typeof element.className === 'string'
            ? element.className.split(/\\s+/).filter(Boolean)
            : [];
          const hasPlainThemeClass = tokens.includes('dark') || tokens.includes('light');
          const hasNamedThemeClass = tokens.includes('theme-dark') || tokens.includes('theme-light');
          const nextTokens = tokens.filter((token) => ![
            'dark', 'light', 'theme-dark', 'theme-light'
          ].includes(token));
          if (element === document.documentElement || hasPlainThemeClass || hasNamedThemeClass) {
            nextTokens.push(hasNamedThemeClass ? `theme-${normalized}` : normalized);
            if (typeof element.className === 'string') element.className = nextTokens.join(' ');
          }
          for (const name of themeAttributes) {
            if (element.hasAttribute(name)) element.setAttribute(name, normalized);
          }
          element.setAttribute('data-mirror-theme', normalized);
        }
        document.documentElement.setAttribute('data-mirror-theme', normalized);
      };
      window.setMirrorTheme = setMirrorTheme;
      const media = window.matchMedia('(prefers-color-scheme: dark)');
      const sync = () => setMirrorTheme(media.matches ? 'dark' : 'light');
      sync();
      document.addEventListener('DOMContentLoaded', sync, { once: true });
      media.addEventListener?.('change', sync);
    })();
  </script>"""


def build_html(base: dict[str, Any], css_name: str, theme_names: list[str]) -> str:
    target = base.get("target") or {}
    target_html = str(base.get("html") or "")
    if not target_html:
        raise RuntimeError("镜像需要目标 outerHTML，但页面没有返回 HTML。")

    script = theme_script(theme_names)
    head = (
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <link rel=\"stylesheet\" href=\"./{css_name}\">\n"
        f"{script}\n"
        "</head>"
    )
    tag = str(target.get("tag") or "").lower()
    if tag == "html":
        lower = target_html.lower()
        head_end = lower.find("</head>")
        if head_end >= 0:
            return target_html[:head_end] + (
                f'  <link rel="stylesheet" href="./{css_name}">\n{script}\n'
            ) + target_html[head_end:]
        html_end = lower.find(">")
        return target_html[: html_end + 1] + head + target_html[html_end + 1 :]
    html_descriptor = target.get("documentElement") or {"tag": "html", "attributes": {}}
    if tag == "body":
        return f"<!doctype html>\n{opening_tag(html_descriptor)}\n{head}\n{target_html}\n</html>\n"

    ancestors = target.get("ancestors") or []
    body = next((item for item in ancestors if item.get("tag") == "body"), {"tag": "body", "attributes": {}})
    body_index = ancestors.index(body) if body in ancestors else -1
    nested = ancestors[body_index + 1 :] if body_index >= 0 else []

    lines = ["<!doctype html>", opening_tag(html_descriptor), head, opening_tag(body)]
    lines.extend(opening_tag(item) for item in nested)
    lines.append(target_html)
    lines.extend(f"</{item.get('tag', 'div')}>" for item in reversed(nested))
    lines.extend(["</body>", "</html>", ""])
    return "\n".join(lines)


def compact_state(capture: dict[str, Any]) -> dict[str, Any]:
    target_element = (capture.get("elements") or [{}])[0]
    return {
        "media": capture.get("media"),
        "targetComputed": target_element.get("computed", {}),
    }


def build_manifest(
    args: argparse.Namespace,
    page: Page,
    frame: Frame,
    base: dict[str, Any],
    captures: list[dict[str, Any]],
    theme_records: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    source_page_url: str,
    source_frame_url: str,
) -> dict[str, Any]:
    classes = merge_class_rules(captures)
    unmatched = sorted(token for token, rules_for_token in classes.items() if not rules_for_token)
    platform = detect_platform(page.url, frame_href(frame))
    render_mode = base.get("renderMode", "dom-css")
    captured_theme_names = []
    for item in theme_records:
        name = item.get("name")
        if name and name not in captured_theme_names:
            captured_theme_names.append(name)
    strategies = sorted({
        str((item.get("controller") or {}).get("strategy"))
        for item in theme_records
        if (item.get("controller") or {}).get("strategy")
    })
    return {
        "schemaVersion": "3.0",
        "mode": "mirror",
        "renderMode": render_mode,
        "source": {
            "platform": platform,
            "sourceType": render_mode,
            "pageUrl": source_page_url,
            "frameUrl": source_frame_url,
            "selector": args.selector,
        },
        "capture": {
            "time": datetime.now(timezone.utc).isoformat(),
            "fullComputed": False,
            "allCss": args.all_css,
            "source": args.source,
            "themes": captured_theme_names,
            "requestedThemes": parse_themes(args.themes),
            "states": args.states,
            "viewports": parse_viewports(args.viewports),
        },
        "target": base.get("target"),
        "discovery": base.get("discovery", {}),
        "coverage": {
            "classTokenCount": len(classes),
            "matchedRuleCount": sum(bool(rules_for_token) for rules_for_token in classes.values()),
            "unmatchedTokens": unmatched,
        },
        "files": {
            "html": "component.html",
            "css": "component.css",
        },
        "css": {
            "sourceRuleCount": len(base.get("cssRules", [])),
            "includedRuleCount": len(rules),
            "keyframes": [item.get("name") for item in base.get("keyframes", [])],
        },
        "assets": base.get("assets", []),
        "fonts": base.get("fonts", []),
        "fontFaces": base.get("fontFaces", []),
        "themes": theme_records,
        "themeHandling": {
            "platform": platform,
            "strategies": strategies,
            "manualSwitch": "window.setMirrorTheme('light'|'dark')",
            "systemFollow": True,
        },
        "states": theme_records[0].get("states", []) if theme_records else [],
        "evidence": [
            {"status": "verified", "item": "component.html 来自公开页面运行时 DOM"},
            {"status": "verified", "item": "component.css 来自目标页面 CSSOM"},
            {"status": "reconstructed", "item": "本地文件只还原视觉和 DOM，不等同于原作者组件源码"},
        ],
    }


def main() -> int:
    args = parse_args()
    try:
        requested_themes = parse_themes(args.themes)
        requested_viewports = parse_viewports(args.viewports)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print(f"无法连接 CDP: {args.cdp_url}\n{exc}", file=sys.stderr)
            return 2

        pages = [page for context in browser.contexts for page in context.pages]
        try:
            page = find_page(pages, args.page_url)
            frame = find_frame(page, args.selector, args.frame_url)
            source_page_url = page.url
            source_frame_url = frame_href(frame)
            base: dict[str, Any] | None = None
            captures: list[dict[str, Any]] = []
            theme_records: list[dict[str, Any]] = []
            theme_variables: dict[str, dict[str, str]] = {}
            viewport_records: list[dict[str, Any]] = []
            frame = find_frame(page, args.selector, args.frame_url)
            states = resolve_states(frame, args.selector, args.states)

            for viewport in requested_viewports:
                viewport_result = apply_viewport(page, viewport)
                viewport_records.append(viewport_result)
                frame = find_frame(page, args.selector, args.frame_url)
                themes, _ = resolve_themes(frame, requested_themes)
                for theme in themes:
                    controller = apply_theme(page, frame, theme)
                    if args.wait_ms:
                        page.wait_for_timeout(args.wait_ms)
                    if not controller.get("captured"):
                        theme_records.append({"name": theme, "viewport": viewport_result, **controller})
                        continue

                    capture_config = capture_args(args, theme)
                    theme_base = capture_state(frame, capture_config)
                    if base is None:
                        base = theme_base
                    theme_captures = [theme_base]
                    state_meta = [{"name": "base", "viewport": viewport_result, "captured": True, **compact_state(theme_base)}]

                    for state in states:
                        if state == "base":
                            continue
                        state_result = apply_state(page, frame, args.selector, state)
                        if not state_result.get("captured"):
                            state_meta.append({"name": state, "viewport": viewport_result, **state_result})
                            continue
                        current = capture_state(frame, capture_config)
                        theme_captures.append(current)
                        state_meta.append({"name": state, "viewport": viewport_result, "captured": True, **compact_state(current)})
                        if state == "active":
                            page.mouse.up()

                    captures.extend(theme_captures)
                    theme_variables[theme] = (
                        theme_base.get("target", {}).get("resolvedCustomProperties") or {}
                    )
                    theme_records.append({
                        "name": theme,
                        "viewport": viewport_result,
                        "captured": True,
                        "controller": controller,
                        "theme": theme_base.get("theme"),
                        "states": state_meta,
                    })

            if base is None:
                raise RuntimeError("没有成功采集任何主题。")

            rules = selected_rules(base, captures, args.all_css)
            captured_theme_names = []
            for item in theme_records:
                name = item.get("name")
                if name and name not in captured_theme_names:
                    captured_theme_names.append(name)
            out_path = Path(args.out)
            out_path.mkdir(parents=True, exist_ok=True)
            css_name = "component.css"
            (out_path / css_name).write_text(
                render_css(
                    rules,
                    (base.get("target") or {}).get("resolvedCustomProperties") or {},
                    source_frame_url,
                    theme_variables,
                    base.get("styleSheets", []),
                ),
                encoding="utf-8",
            )
            (out_path / "component.html").write_text(
                build_html(base, css_name, captured_theme_names),
                encoding="utf-8",
            )
            manifest = build_manifest(
                args,
                page,
                frame,
                base,
                captures,
                theme_records,
                rules,
                source_page_url,
                source_frame_url,
            )
            (out_path / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps({
                "out": str(out_path),
                "html": str(out_path / "component.html"),
                "css": str(out_path / "component.css"),
                "manifest": str(out_path / "manifest.json"),
                "classTokens": manifest["coverage"]["classTokenCount"],
                "matchedRules": manifest["coverage"]["matchedRuleCount"],
                "includedCssRules": len(rules),
                "themes": captured_theme_names,
                "states": [item.get("name") for item in manifest.get("states", [])],
            }, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(f"生成本地镜像失败：{exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
