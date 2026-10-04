#!/usr/bin/env python3
"""输出已核验配色基线，或计算实际 sRGB 颜色对的 WCAG 对比度；仅用标准库。"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

PALETTES = Path(__file__).resolve().parents[1] / "references" / "color-palettes.json"


def parse_color(value):
    value = value.strip().lower()
    if value == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if re.fullmatch(r"#[0-9a-f]{3,4}|#[0-9a-f]{6}|#[0-9a-f]{8}", value):
        digits = value[1:]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        numbers = [int(digits[i:i + 2], 16) / 255 for i in range(0, len(digits), 2)]
        return tuple(numbers if len(numbers) == 4 else numbers + [1.0])
    match = re.fullmatch(r"rgba?\(([^()]+)\)", value)
    if not match:
        raise ValueError("颜色须为 hex 或 rgb/rgba；var、hsl、oklch 先用浏览器解析为实际 sRGB 值")
    parts = re.split(r"\s*[,/]\s*|\s+", match[1].strip())
    if len(parts) not in (3, 4):
        raise ValueError("rgb/rgba 须包含三个通道和可选透明度")
    channels = [float(p[:-1]) / 100 if p.endswith("%") else float(p) / 255 for p in parts[:3]]
    alpha = 1.0 if len(parts) == 3 else float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
    numbers = channels + [alpha]
    if any(not math.isfinite(n) or not 0 <= n <= 1 for n in numbers):
        raise ValueError("颜色通道或透明度超出范围")
    return tuple(numbers)


def composite(foreground, background):
    alpha = foreground[3] + background[3] * (1 - foreground[3])
    if alpha == 0:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple((foreground[i] * foreground[3] + background[i] * background[3] * (1 - foreground[3])) / alpha for i in range(3)) + (alpha,)


def luminance(color):
    channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in color[:3]]
    return sum(a * b for a, b in zip(channels, (0.2126, 0.7152, 0.0722)))


def check_pair(pair):
    background = parse_color(pair["background"])
    if background[3] < 1:
        if "canvas" not in pair:
            raise ValueError("透明背景须提供不透明 canvas；多层背景先在浏览器解析或合成")
        canvas = parse_color(pair["canvas"])
        if canvas[3] != 1:
            raise ValueError("canvas 必须不透明")
        background = composite(background, canvas)
    foreground = composite(parse_color(pair["foreground"]), background)
    bright, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    ratio = (bright + 0.05) / (dark + 0.05)
    minimum = float(pair.get("minimum", 4.5))
    if not math.isfinite(minimum) or minimum < 1:
        raise ValueError("minimum 须为至少 1 的有限数值")
    return {"name": pair.get("name", "color-pair"), "ratio": round(ratio, 4), "minimum": minimum, "passes": ratio >= minimum}


def preset_pairs(tokens):
    pairs = []
    def add(name, foreground, background, minimum=4.5):
        pairs.append({"name": name, "foreground": tokens[foreground], "background": tokens[background], "minimum": minimum})
    for surface in ("page", "surface", "surface-subtle", "surface-hover"):
        for text in ("text", "text-secondary", "link"):
            add(text + "/" + surface, text, surface)
        for control in ("control-border", "focus"):
            add(control + "/" + surface, control, surface, 3.0)
        for status in ("success", "warning", "error"):
            add(status + "/" + surface, status + "-text", surface)
    for state in ("action", "action-hover", "action-active"):
        add("on-" + state, "on-action", state)
    add("selection", "on-selected", "selected")
    for status in ("success", "warning", "error"):
        add(status + "/tint", status + "-text", status + "-bg")
    return pairs


def main():
    presets = json.loads(PALETTES.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    palette = sub.add_parser("palette", help="输出指定基线；不会修改项目")
    palette.add_argument("name", choices=presets)
    palette.add_argument("--css", action="store_true", help="输出 --color-* CSS 变量供项目映射")
    check = sub.add_parser("check", help="检查基线或给定颜色对；失败退出 1，输入错误退出 2")
    target = check.add_mutually_exclusive_group(required=True)
    target.add_argument("--preset", choices=presets)
    target.add_argument("--pairs", help="颜色对 JSON 数组路径，或 - 从 stdin 读取")
    args = parser.parse_args()
    try:
        if args.command == "palette":
            entry = presets[args.name]
            if args.css:
                print(":root {\n" + "\n".join("  --color-" + name + ": " + value + ";" for name, value in entry["tokens"].items()) + "\n}")
            else:
                print(json.dumps(entry, ensure_ascii=False, indent=2))
            return 0
        if args.preset:
            pairs = preset_pairs(presets[args.preset]["tokens"])
        else:
            pairs = json.loads(sys.stdin.read() if args.pairs == "-" else Path(args.pairs).read_text(encoding="utf-8"))
        if not isinstance(pairs, list) or not pairs:
            raise ValueError("颜色对输入须为非空 JSON 数组")
        results = [check_pair(pair) for pair in pairs]
        passed = all(result["passes"] for result in results)
        print(json.dumps({"passes": passed, "pairs": results}, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    sys.exit(main())
