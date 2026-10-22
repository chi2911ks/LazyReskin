from __future__ import annotations

import math
import re
import struct
import xml.etree.ElementTree as ET

from .common import ReskinError
from .source import parse_xml


def android_color(value: str, css=False) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{3,8}", value) or len(value) not in (4, 5, 7, 9):
        raise ReskinError(f"Unsupported color: {value}")
    digits = value[1:].upper()
    if len(digits) in (3, 4):
        digits = "".join(char * 2 for char in digits)
    if len(digits) == 6:
        digits = "FF" + digits
    elif css:
        digits = digits[-2:] + digits[:-2]
    return "#" + digits


def figma_color(node: dict, text=False) -> str:
    fills = node.get("text", {}).get("fills", node.get("fills", [])) if text else node.get("fills", [])
    if not isinstance(fills, list):
        raise ReskinError("Mixed fills need an explicit mapping")
    paints = [paint for paint in fills if paint.get("visible", True)]
    if len(paints) != 1 or paints[0].get("type") != "SOLID":
        raise ReskinError("Only a single visible SOLID paint can map to a color; layered/gradient/image fills need a separate drawable")
    paint = paints[0]
    opacity = node.get("opacity", 1)
    color = paint.get("color")
    if color:
        channels = [color.get(key, 0) for key in ("r", "g", "b")]
        alpha = color.get("a", 1) * paint.get("opacity", 1) * opacity
    elif paint.get("hex"):
        digits = android_color(paint["hex"], css=True)[1:]
        alpha = int(digits[:2], 16) / 255 * opacity
        if len(paint["hex"]) in (4, 7):
            alpha *= paint.get("opacity", 1)
        channels = [int(digits[i:i + 2], 16) / 255 for i in (2, 4, 6)]
    else:
        raise ReskinError("Paint has neither color nor hex")
    values = [alpha] + channels
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ReskinError("Invalid Figma color channels/opacity")
    return "#" + "".join(f"{int(value * 255 + .5):02X}" for value in values)


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ReskinError("Invalid PNG asset")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height or width * height > 50_000_000:
        raise ReskinError("Unsupported PNG dimensions")
    return width, height


def svg_to_vector(raw: bytes) -> bytes:
    """Deliberately strict subset: groups and paths, no silent dropped graphics."""
    root = parse_xml(raw, "SVG")
    android = "http://schemas.android.com/apk/res/android"
    ET.register_namespace("android", android)
    a = lambda key: "{" + android + "}" + key
    allowed_tags = {"svg", "g", "path", "title", "desc"}
    allowed_attributes = {"id", "width", "height", "viewBox", "version", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "fill-rule", "clip-rule", "opacity", "fill-opacity", "stroke-opacity", "d"}
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag not in allowed_tags:
            raise ReskinError(f"SVG {tag} not supported; provide a verified PNG fallback")
        for key in element.attrib:
            if key not in allowed_attributes:
                raise ReskinError(f"SVG attribute {key} not supported; provide a verified PNG fallback")
    box = re.split(r"[\s,]+", root.get("viewBox", "").strip())
    if len(box) != 4:
        raise ReskinError("SVG requires a four-number viewBox")
    try:
        x, y, width, height = map(float, box)
    except ValueError as exc:
        raise ReskinError("Invalid SVG viewBox") from exc
    if x != 0 or y != 0 or width <= 0 or height <= 0 or not all(map(math.isfinite, (x, y, width, height))):
        raise ReskinError("SVG viewBox needs positive dimensions and origin 0,0")

    def dimension(key, fallback):
        value = root.get(key, str(fallback)).removesuffix("px")
        if not re.fullmatch(r"\d+(?:\.\d+)?", value) or float(value) <= 0:
            raise ReskinError(f"Unsupported SVG {key}: {value}")
        return value

    vector = ET.Element("vector", {a("width"): dimension("width", width) + "dp", a("height"): dimension("height", height) + "dp", a("viewportWidth"): str(width), a("viewportHeight"): str(height)})

    def paint(value):
        if value == "none":
            return "#00000000"
        return android_color({"black": "#000000", "white": "#ffffff", "transparent": "#00000000"}.get(value, value), css=True)

    def walk(element, inherited, alpha):
        attrs = dict(inherited)
        attrs.update(element.attrib)
        try:
            alpha *= float(element.get("opacity", 1))
            if not 0 <= alpha <= 1:
                raise ValueError()
            if element.tag.split("}")[-1] == "path":
                if not element.get("d"):
                    raise ReskinError("SVG path has no d")
                values = {a("pathData"): element.get("d"), a("fillColor"): paint(attrs.get("fill", "black")), a("strokeColor"): paint(attrs.get("stroke", "none"))}
                for key, output in (("fill-opacity", "fillAlpha"), ("stroke-opacity", "strokeAlpha")):
                    value = float(attrs.get(key, 1)) * alpha
                    if not 0 <= value <= 1:
                        raise ValueError()
                    values[a(output)] = str(value)
                for key, output in (("stroke-width", "strokeWidth"), ("stroke-linecap", "strokeLineCap"), ("stroke-linejoin", "strokeLineJoin"), ("stroke-miterlimit", "strokeMiterLimit")):
                    if key in attrs:
                        values[a(output)] = attrs[key]
                if attrs.get("fill-rule") == "evenodd":
                    values[a("fillType")] = "evenOdd"
                ET.SubElement(vector, "path", values)
            for child in element:
                walk(child, attrs, alpha)
        except ValueError as exc:
            raise ReskinError("Invalid SVG opacity") from exc

    walk(root, {}, 1.0)
    if not list(vector):
        raise ReskinError("SVG contains no renderable paths")
    return ET.tostring(vector, encoding="utf-8", xml_declaration=True) + b"\n"
