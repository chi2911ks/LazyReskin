from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


class ReskinError(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ReskinError(f"Cannot read JSON {path}: {exc}") from exc


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".lr-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def contained(root: Path, relative: str) -> Path:
    raw = str(relative).replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ".." in raw.split("/"):
        raise ReskinError(f"Unsafe relative path: {relative}")
    root = root.resolve()
    target = (root / raw).resolve()
    if not target.is_relative_to(root):
        raise ReskinError(f"Path escapes root: {relative}")
    return target


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", str(value).lower()).strip("_")[:40]
    if not value:
        raise ReskinError("Expected a non-empty name")
    return value if value[0].isalpha() else "skin_" + value


def load_profile(path: Path) -> dict:
    profile = read_json(path)
    if not isinstance(profile, dict) or not profile.get("source"):
        raise ReskinError("Profile must contain source")
    profile = dict(profile)
    profile["source"] = str((path.parent / profile["source"]).resolve())
    root = Path(profile["source"])
    if not root.is_dir():
        raise ReskinError(f"Source directory does not exist: {root}")
    profile["module"] = profile.get("module", "app")
    contained(root, profile["module"])
    profile["resource_roots"] = profile.get("resource_roots", ["src/main/res"])
    if not isinstance(profile["resource_roots"], list) or not profile["resource_roots"]:
        raise ReskinError("resource_roots must be a non-empty list")
    for entry in profile["resource_roots"]:
        contained(contained(root, profile["module"]), entry)
    return profile
