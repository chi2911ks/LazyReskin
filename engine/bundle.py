from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from .common import ReskinError, contained, digest


class Bundle:
    """Read directories and ZIP bundles without extracting untrusted paths."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self._zip = None
        if path.is_dir():
            self.names = sorted(p.relative_to(self.path).as_posix() for p in path.rglob("*") if p.is_file())
        elif zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
            members = [entry for entry in self._zip.infolist() if not entry.is_dir()]
            self.names = [entry.filename for entry in members]
            if len(self.names) != len(set(self.names)):
                self.close()
                raise ReskinError("Duplicate ZIP member names")
            if any(entry.file_size > 128 * 1024 * 1024 for entry in members) or sum(entry.file_size for entry in members) > 1024**3:
                self.close()
                raise ReskinError("Bundle exceeds size limits")
        else:
            raise ReskinError(f"Not a bundle directory or ZIP: {path}")
        for name in self.names:
            contained(self.path if self.path.is_dir() else self.path.parent, name)
        # ZIPs may wrap their content in one named folder.
        self.prefix = ""
        if "meta.json" not in self.names:
            candidates = [name[:-len("meta.json")] for name in self.names if name.endswith("/meta.json")]
            if len(candidates) == 1:
                self.prefix = candidates[0]
        self.files = sorted(name[len(self.prefix):] for name in self.names if name.startswith(self.prefix))
        self.meta = self.json("meta.json") if "meta.json" in self.files else {}
        self.screens = {}
        for name in self.files:
            if name.startswith("screens/") and name.endswith(".json"):
                screen = self.json(name)
                nodes = screen.get("flattened")
                if not isinstance(nodes, list) or not nodes:
                    raise ReskinError(f"Screen has no flattened nodes: {name}")
                ids = [node.get("id") for node in nodes]
                if any(not isinstance(node_id, str) for node_id in ids) or len(ids) != len(set(ids)):
                    raise ReskinError(f"Missing/duplicate node IDs: {name}")
                self.screens[name] = screen
        if not self.screens:
            raise ReskinError("Bundle contains no screens/*.json")
        manifest = self.json("assets.manifest.json") if "assets.manifest.json" in self.files else {}
        if isinstance(manifest, list):
            self.assets = manifest
        elif isinstance(manifest, dict):
            version = manifest.get("schemaVersion", 2)
            if version not in (1, 2):
                raise ReskinError(f"Unsupported bundle schemaVersion: {version}")
            self.assets = manifest.get("assets", manifest.get("assetExports", []))
        else:
            raise ReskinError("Invalid asset manifest")
        if not isinstance(self.assets, list):
            raise ReskinError("Manifest assets must be a list")

    def close(self):
        if self._zip:
            self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, name: str) -> bytes:
        contained(self.path if self.path.is_dir() else self.path.parent, name)
        if name not in self.files:
            raise ReskinError(f"Bundle file missing: {name}")
        if self._zip:
            return self._zip.read(self.prefix + name)
        return contained(self.path, self.prefix + name).read_bytes()

    def json(self, name):
        try:
            return json.loads(self.read(name).decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ReskinError(f"Invalid bundle JSON {name}: {exc}") from exc

    def screen(self, name: str) -> dict:
        name = name if name.startswith("screens/") else "screens/" + name
        if name not in self.screens:
            raise ReskinError(f"Unknown Figma screen: {name}")
        return self.screens[name]

    def reference(self, screen_name: str) -> str | None:
        candidate = "screenshots/" + PurePosixPath(screen_name).stem + ".png"
        return candidate if candidate in self.files else None

    def asset(self, filename: str) -> tuple[bytes, dict]:
        matches = [item for item in self.assets if item.get("filename") == filename]
        if len(matches) != 1 or matches[0].get("status", "exported") != "exported":
            raise ReskinError(f"Asset needs exactly one successful manifest record: {filename}")
        return self.read("assets/" + filename), matches[0]

    def summary(self) -> dict:
        diagnostics = []
        asset_files = [name for name in self.files if name.startswith("assets/")]
        if asset_files and "assets.manifest.json" not in self.files:
            diagnostics.append({"code": "asset-manifest-missing", "asset_files": len(asset_files), "message": "Legacy assets can be inspected, but patching needs a successful manifest record"})
        for filename, screen in self.screens.items():
            ids = {node["id"] for node in screen["flattened"]}
            dangling = [(node["id"], child) for node in screen["flattened"] for child in node.get("childIds", []) if child not in ids]
            if dangling:
                diagnostics.append({"screen": filename, "code": "pruned-or-missing-children", "count": len(dangling)})
            if not self.reference(filename):
                diagnostics.append({"screen": filename, "code": "missing-reference"})
        for asset in self.assets:
            if asset.get("status", "exported") != "exported" or "assets/" + asset.get("filename", "") not in self.files:
                diagnostics.append({"code": "asset-incomplete", "asset": asset.get("filename"), "status": asset.get("status")})
        return {
            "screens": [{"file": name, "root": data.get("root"), "state": data.get("screenState"), "nodes": len(data["flattened"]), "reference": self.reference(name)} for name, data in self.screens.items()],
            "assets": len(self.assets), "asset_files": len(asset_files), "diagnostics": diagnostics,
            "fingerprints": {name: digest(self.read(name)) for name in self.files},
        }
