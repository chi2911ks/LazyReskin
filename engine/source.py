from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import ReskinError, contained, digest

NAMESPACES = {"http://schemas.android.com/apk/res/android": "android", "http://schemas.android.com/apk/res-auto": "app", "http://schemas.android.com/tools": "tools"}
REFERENCE = re.compile(r"@(?:\+)?(?!android:)([a-zA-Z_][\w]*)/([\w.]+)")
RUNTIME = re.compile(r"set(?:Background\w*|TextColor|ImageResource|ImageDrawable|ColorFilter|Padding|TextSize|Typeface|Visibility)|\.(?:background|backgroundTintList|imageTintList|visibility|textSize|typeface)\s*=")


def attr_name(name):
    if name.startswith("{"):
        namespace, local = name[1:].split("}", 1)
        return NAMESPACES.get(namespace, namespace) + ":" + local
    return name


def parse_xml(data: bytes, description="XML"):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ReskinError(f"DTD/entities are not supported: {description}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ReskinError(f"Invalid {description}: {exc}") from exc


def index_source(profile: dict) -> dict:
    root = Path(profile["source"])
    module = contained(root, profile["module"])
    if not module.is_dir():
        raise ReskinError(f"Module does not exist: {module}")
    layouts, files, consumers, runtime, diagnostics, resources = {}, {}, {}, [], [], {}
    for path in sorted((module / "src").rglob("*")):
        if not path.is_file() or path.suffix not in (".xml", ".kt", ".java"):
            continue
        relative = path.relative_to(root).as_posix()
        # Reject symlink escapes before reading.
        contained(root, relative)
        raw = path.read_bytes()
        files[relative] = digest(raw)
        text = raw.decode("utf-8-sig", errors="replace")
        references = set(REFERENCE.findall(text))
        references.update(re.findall(r"\bR\.([a-z_]+)\.([\w]+)", text))
        for resource_type, name in references:
            consumers.setdefault(resource_type + "/" + name, []).append(relative)
        if path.suffix == ".xml":
            try:
                document = parse_xml(raw, relative)
            except ReskinError as exc:
                diagnostics.append({"code": "invalid-source-xml", "file": relative, "message": str(exc)})
                continue
            if path.parent.name.startswith("layout"):
                elements = []
                for position, element in enumerate(document.iter()):
                    attrs = {attr_name(key): value for key, value in element.attrib.items()}
                    view_id = attrs.get("android:id", "").split("/")[-1]
                    elements.append({"index": position, "tag": element.tag, "view": view_id, "attributes": attrs})
                layouts[relative] = elements
                resources.setdefault("layout/" + path.stem, []).append(relative)
            elif path.parent.name.startswith("values"):
                for element in document:
                    if "name" in element.attrib:
                        resource_type = element.attrib.get("type", element.tag)
                        resources.setdefault(resource_type + "/" + element.attrib["name"], []).append(relative)
            elif path.parent.name.startswith(("drawable", "mipmap", "color")):
                resources.setdefault(path.parent.name.split("-")[0] + "/" + path.stem, []).append(relative)
        else:
            for number, line in enumerate(text.splitlines(), 1):
                if RUNTIME.search(line):
                    runtime.append({"file": relative, "line": number, "text": line.strip()[:500]})
    # Binary resources also participate in dependency analysis, not text parsing.
    for resource_root in profile["resource_roots"]:
        res = contained(module, resource_root)
        if not res.is_dir():
            raise ReskinError(f"Resource root does not exist: {res}")
        for path in sorted(res.rglob("*")):
            if path.is_file() and path.suffix != ".xml":
                relative = path.relative_to(root).as_posix()
                contained(root, relative)
                files[relative] = digest(path.read_bytes())
                resources.setdefault(path.parent.name.split("-")[0] + "/" + path.name.split(".")[0], []).append(relative)
    for directory in (root, module, root / "gradle"):
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file() and (path.suffix in (".kts", ".gradle", ".properties", ".toml") or path.name.startswith("gradlew")):
                    relative = path.relative_to(root).as_posix()
                    contained(root, relative)
                    files[relative] = digest(path.read_bytes())
    return {"source": str(root), "module": profile["module"], "files": files, "layouts": layouts, "resources": resources, "consumers": consumers, "runtime_overrides": runtime, "diagnostics": diagnostics}


def affected_consumers(index: dict, resource: str) -> list[str]:
    result, queue, visited = set(), [resource], set()
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for filename in index["consumers"].get(current, []):
            result.add(filename)
            for key, definitions in index["resources"].items():
                if filename in definitions:
                    queue.append(key)
    return sorted(result)


def suggestions(index: dict, nodes: list[dict]) -> list[dict]:
    def clean(value):
        return re.sub(r"[^a-z0-9]", "", value.lower())

    results = []
    for node in nodes:
        target = clean(node.get("name", ""))
        if len(target) < 3:
            continue
        for filename, elements in index["layouts"].items():
            for element in elements:
                view = clean(re.sub(r"^(?:btn|tv|txt|img|iv|layout|root|ic)(?=[A-Z_])", "", element["view"]))
                if view and view == target:
                    results.append({"node": node["id"], "node_name": node["name"], "layout": filename, "view": element["view"], "status": "suggestion-only", "reason": "normalized-name-match"})
    return results
