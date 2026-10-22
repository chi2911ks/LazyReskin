from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from .assets import android_color, figma_color, png_dimensions, svg_to_vector
from .bundle import Bundle
from .common import ReskinError, atomic_write, contained, digest, slug, write_json
from .source import affected_consumers, index_source, suggestions
from .xmlpatch import patch_attributes

COLOR_ATTRS = {"android:background", "android:textColor", "android:textColorHint", "android:tint", "android:backgroundTint", "app:tint", "app:backgroundTint", "app:cardBackgroundColor", "app:strokeColor"}
IMAGE_ATTRS = {"android:src", "app:srcCompat", "android:background"}


def create_plan(profile: dict, skin: dict, bundle: Bundle, run: Path, screens=None):
    if run.exists() and any(run.iterdir()):
        raise ReskinError(f"Run directory must be empty: {run}")
    if skin.get("mode", "colors-icons") != "colors-icons":
        raise ReskinError("This release supports colors-icons only; appearance/layout require a later adapter")
    name = slug(skin.get("name", "skin"))
    root = Path(profile["source"])
    module = contained(root, profile["module"])
    if run.resolve().is_relative_to((module / "src").resolve()):
        raise ReskinError("Run artifacts cannot be written inside Android source sets")
    if bundle.path.is_dir() and run.resolve().is_relative_to(bundle.path):
        raise ReskinError("Run artifacts cannot be written inside the input bundle")
    index = index_source(profile)
    issues, operations, mappings, changes, references, targets = [], [], [], {}, {}, []
    planned_files = {}
    screen_defs = skin.get("screens", [])
    if not isinstance(screen_defs, list) or not screen_defs:
        raise ReskinError("Skin must have a non-empty screens list")
    keys = [item.get("key") for item in screen_defs]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ReskinError("Skin screen keys must be unique and non-empty")
    if screens:
        unknown = set(screens) - set(keys)
        if unknown:
            raise ReskinError(f"Unknown screen keys: {sorted(unknown)}")
        screen_defs = [item for item in screen_defs if item["key"] in screens]

    def generated(relative, raw):
        path = contained(root, relative)
        if relative in planned_files and planned_files[relative] != raw:
            raise ReskinError(f"Conflicting generated resource: {relative}")
        if path.exists() and path.read_bytes() != raw:
            raise ReskinError(f"Generated resource was modified or collides: {relative}")
        planned_files[relative] = raw

    for screen in screen_defs:
        key = screen["key"]
        layout = screen.get("layout", "")
        path = contained(root, layout)
        allowed_roots = [contained(module, entry) for entry in profile["resource_roots"]]
        if not any(path.is_relative_to(res) and path.parent.name.startswith("layout") for res in allowed_roots):
            raise ReskinError(f"Layout outside configured resource roots: {layout}")
        if layout not in index["layouts"]:
            raise ReskinError(f"Unknown source layout: {layout}")
        res_root = path.parent.parent
        data = bundle.screen(screen.get("figma", ""))
        nodes = {node["id"]: node for node in data["flattened"]}
        reference = screen.get("reference") or bundle.reference(screen["figma"])
        if reference:
            png_dimensions(bundle.read(reference))
            ref_file = "references/" + slug(key) + ".png"
            references[ref_file] = bundle.read(reference)
        else:
            ref_file = None
            issues.append({"severity": "warning", "code": "missing-reference", "screen": key})
        targets.append({"key": key, "layout": layout, "reference": ref_file, "capture": screen.get("capture", {}), "comparison": screen.get("comparison", {}), "affected_consumers": affected_consumers(index, "layout/" + path.stem)})
        if not screen.get("mappings"):
            issues.append({"severity": "error", "code": "mapping-required", "screen": key, "suggestions": suggestions(index, data["flattened"])})
        for mapping in screen.get("mappings", []):
            view = mapping.get("view")
            node_id = mapping.get("node")
            record = {"screen": key, "layout": layout, "view": view, "node": node_id, "status": "unresolved"}
            mappings.append(record)
            try:
                if node_id not in nodes:
                    raise ReskinError(f"Unknown Figma node: {node_id}")
                node = nodes[node_id]
                if node.get("visible") is False or any(nodes.get(parent.get("id"), {}).get("visible") is False for parent in node.get("parentChain", [])):
                    raise ReskinError("Mapped node is hidden; select a visible target state")
                matches = [entry for entry in index["layouts"][layout] if entry["view"] == view and view]
                if len(matches) != 1:
                    raise ReskinError(f"Expected exactly one view {view} in {layout}")
                element = matches[0]
                properties = mapping.get("properties", {})
                if not isinstance(properties, dict) or not properties:
                    raise ReskinError("Mapping needs explicit properties")
                for attribute, spec in properties.items():
                    if attribute not in COLOR_ATTRS | IMAGE_ATTRS:
                        raise ReskinError(f"Attribute outside colors-icons scope: {attribute}")
                    tag = element["tag"].rsplit(".", 1)[-1]
                    if attribute in ("android:src", "app:srcCompat") and not tag.endswith(("ImageView", "ImageButton")) and mapping.get("view_type_reviewed") is not True:
                        raise ReskinError(f"{tag} is not a recognized image view; review custom view support explicitly")
                    override_hits = [item for item in index["runtime_overrides"] if re.search(r"\b" + re.escape(view) + r"\b", item["text"])]
                    if override_hits and mapping.get("runtime_reviewed") is not True:
                        issues.append({"severity": "error", "code": "runtime-review-required", "screen": key, "view": view, "evidence": override_hits})
                    if isinstance(spec, dict) and "asset" in spec:
                        if attribute not in IMAGE_ATTRS:
                            raise ReskinError(f"Asset is invalid for {attribute}")
                        raw, asset = bundle.asset(spec["asset"])
                        extension = Path(spec["asset"]).suffix.lower()
                        resource = "lr_" + name + "_" + slug(Path(spec["asset"]).stem) + "_" + digest(raw)[:10]
                        if extension == ".svg":
                            raw = svg_to_vector(raw)
                            folder, extension = "drawable", ".xml"
                        elif extension == ".png":
                            png_dimensions(raw)
                            scale = asset.get("scale", bundle.meta.get("pngScale", 2))
                            densities = {1: "mdpi", 2: "xhdpi", 3: "xxhdpi", 4: "xxxhdpi"}
                            if scale not in densities:
                                raise ReskinError(f"Unsupported raster scale: {scale}")
                            folder = "drawable-" + densities[scale]
                        else:
                            raise ReskinError("Only manifest PNG and supported SVG assets can be patched")
                        generated((res_root / folder / (resource + extension)).relative_to(root).as_posix(), raw)
                        value = "@drawable/" + resource
                    else:
                        if attribute not in COLOR_ATTRS:
                            raise ReskinError(f"Color is invalid for {attribute}")
                        if spec == "fill":
                            color = figma_color(node)
                        elif spec == "textColor":
                            if node.get("type") != "TEXT":
                                raise ReskinError("textColor needs a TEXT node")
                            color = figma_color(node, text=True)
                        elif isinstance(spec, dict) and set(spec) == {"value"}:
                            color = android_color(spec["value"])
                        else:
                            raise ReskinError(f"Unsupported color mapping: {spec}")
                        resource = "lr_" + name + "_" + color[1:].lower()
                        raw = f'<?xml version="1.0" encoding="utf-8"?>\n<!-- Generated by LazyReskin; content-addressed color. -->\n<resources>\n    <color name="{resource}">{color}</color>\n</resources>\n'.encode()
                        generated((res_root / "values" / (resource + ".xml")).relative_to(root).as_posix(), raw)
                        value = "@color/" + resource
                    previous = element["attributes"].get(attribute)
                    if attribute == "android:background" and value.startswith("@color/") and previous and previous.startswith("@drawable/"):
                        raise ReskinError("Replacing a drawable background with a solid color may remove shape/stroke; map backgroundTint or a verified asset instead")
                    marker = (element["index"], attribute)
                    per_file = changes.setdefault(layout, {})
                    if marker in per_file and per_file[marker]["value"] != value:
                        raise ReskinError(f"Conflicting target values for {view}.{attribute}")
                    per_file[marker] = {"index": element["index"], "attribute": attribute, "value": value}
                    operations.append({"screen": key, "node": node_id, "layout": layout, "view": view, "attribute": attribute, "before": previous, "after": value})
                record["status"] = "mapped"
            except (ReskinError, TypeError, ValueError, KeyError) as exc:
                issues.append({"severity": "error", "code": "mapping-invalid", "screen": key, "node": node_id, "view": view, "message": str(exc)})
    for layout, attributes in changes.items():
        raw = contained(root, layout).read_bytes()
        planned_files[layout] = patch_attributes(raw, list(attributes.values()))
    files, diff = [], []
    for filename, after in sorted(planned_files.items()):
        target = contained(root, filename)
        before = target.read_bytes() if target.exists() else None
        if before == after:
            continue
        files.append({"path": filename, "before": digest(before) if before is not None else None, "after": digest(after)})
        atomic_write(contained(run / "staged", filename), after)
        if before is not None:
            atomic_write(contained(run / "backups", filename), before)
        if filename.endswith(".xml"):
            diff.extend(difflib.unified_diff((before or b"").decode("utf-8-sig").splitlines(True), after.decode("utf-8-sig").splitlines(True), fromfile="a/" + filename, tofile="b/" + filename))
        else:
            diff.append(f"Binary resource added: {filename}\n")
    errors = [issue for issue in issues if issue["severity"] == "error"]
    plan = {"schemaVersion": 1, "mode": "colors-icons", "skin": name, "source": str(root), "state": "needs-input" if errors else "planned", "profile": profile, "targets": targets, "files": files, "operations": operations, "issues": issues, "source_fingerprint": index["files"], "bundle_fingerprint": bundle.summary()["fingerprints"]}
    write_json(run / "source-index.json", index)
    write_json(run / "mapping.json", mappings)
    write_json(run / "change-plan.json", plan)
    write_json(run / "inputs.json", {"profile": profile, "skin": skin, "bundle": str(bundle.path)})
    for filename, raw in references.items():
        atomic_write(contained(run, filename), raw)
    atomic_write(run / "patch.diff", "".join(diff).encode())
    write_json(run / "checkpoint.json", {"state": plan["state"], "iteration": 0, "applied": False})
    return plan
