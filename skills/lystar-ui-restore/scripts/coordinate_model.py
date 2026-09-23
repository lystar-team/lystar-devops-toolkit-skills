#!/usr/bin/env python3
"""Coordinate transforms between CSS viewport pixels and screenshot image pixels."""
from __future__ import annotations

from typing import Any

from restore_core import ContractError


def parse_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return {
            "x": float(value["x"]),
            "y": float(value["y"]),
            "width": float(value["width"]),
            "height": float(value["height"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def build_transform(capture: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    viewport = capture.get("viewport") or config.get("viewport")
    readiness = capture.get("readiness")
    if not isinstance(viewport, dict) or not isinstance(readiness, dict):
        raise ContractError("capture viewport/readiness is required for coordinate normalization")
    image_size = readiness.get("imageSize")
    if not isinstance(image_size, list) or len(image_size) != 2:
        raise ContractError("capture.readiness.imageSize must contain width and height")
    css_width = float(viewport.get("width") or 0)
    css_height = float(viewport.get("height") or 0)
    image_width = float(image_size[0])
    image_height = float(image_size[1])
    if min(css_width, css_height, image_width, image_height) <= 0:
        raise ContractError("coordinate dimensions must be positive")

    mode = str(config.get("screenshotMode", "viewport"))
    scroll = config.get("scroll") if isinstance(config.get("scroll"), dict) else {}
    clip = parse_bbox(scroll.get("clip"))
    if mode in {"clip", "scroll-segment"}:
        if clip is None or clip["width"] <= 0 or clip["height"] <= 0:
            raise ContractError(f"{mode} requires a positive scroll.clip bbox")
        scale_x = image_width / clip["width"]
        scale_y = image_height / clip["height"]
        translate_x = -clip["x"] * scale_x
        translate_y = -clip["y"] * scale_y
        origin = "css-viewport-px"
    elif mode == "fullPage":
        scale_x = image_width / css_width
        scale_y = scale_x
        translate_x = float(scroll.get("x", 0)) * scale_x
        translate_y = float(scroll.get("y", 0)) * scale_y
        origin = "css-document-px"
    else:
        scale_x = image_width / css_width
        scale_y = image_height / css_height
        translate_x = 0.0
        translate_y = 0.0
        origin = "css-viewport-px"

    return {
        "sourceSpace": "css-viewport-px",
        "targetSpace": "render-image-px",
        "screenshotMode": mode,
        "sourceOrigin": origin,
        "scaleX": scale_x,
        "scaleY": scale_y,
        "translateX": translate_x,
        "translateY": translate_y,
        "deviceScaleFactor": viewport.get("deviceScaleFactor"),
        "imageSize": [int(image_width), int(image_height)],
        "cssViewport": {"width": css_width, "height": css_height},
        "scroll": {"x": float(scroll.get("x", 0)), "y": float(scroll.get("y", 0))},
        "clip": clip,
    }


def transform_bbox(value: Any, transform: dict[str, Any]) -> dict[str, float] | None:
    bbox = parse_bbox(value)
    if bbox is None:
        return None
    scale_x = float(transform["scaleX"])
    scale_y = float(transform["scaleY"])
    translate_x = float(transform["translateX"])
    translate_y = float(transform["translateY"])
    return {
        "x": round(bbox["x"] * scale_x + translate_x, 3),
        "y": round(bbox["y"] * scale_y + translate_y, 3),
        "width": round(bbox["width"] * scale_x, 3),
        "height": round(bbox["height"] * scale_y, 3),
    }
