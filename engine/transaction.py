from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .common import ReskinError, atomic_write, contained, digest, read_json, write_json
from .source import index_source


@contextmanager
def lock(key: Path):
    filename = Path(tempfile.gettempdir()) / ("lazyreskin-" + digest(str(key.resolve()).encode())[:24] + ".lock")
    try:
        fd = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ReskinError(f"Another operation holds {filename}; if its process stopped, inspect and remove this lock") from exc
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        filename.unlink(missing_ok=True)


def load_plan(run: Path):
    plan = read_json(run / "change-plan.json")
    root = Path(plan["source"]).resolve()
    if str(root) != plan["profile"]["source"]:
        raise ReskinError("Plan and profile source disagree")
    module = contained(root, plan["profile"]["module"])
    res_roots = [contained(module, entry) for entry in plan["profile"]["resource_roots"]]
    for entry in plan["files"]:
        target = contained(root, entry["path"])
        if not any(target.is_relative_to(res) for res in res_roots):
            raise ReskinError("Patch escapes configured resource roots")
        staged = contained(run / "staged", entry["path"])
        if not staged.is_file() or digest(staged.read_bytes()) != entry["after"]:
            raise ReskinError(f"Staged file corrupted: {entry['path']}")
        if entry["before"] is not None:
            backup = contained(run / "backups", entry["path"])
            if not backup.is_file() or digest(backup.read_bytes()) != entry["before"]:
                raise ReskinError(f"Backup corrupted: {entry['path']}")
    return plan


def apply_plan(run: Path):
    plan = load_plan(run)
    root = Path(plan["source"])
    with lock(root), lock(run):
        checkpoint = read_json(run / "checkpoint.json")
        if checkpoint.get("applied"):
            if index_source(plan["profile"])["files"] != checkpoint.get("source_after"):
                raise ReskinError("Source conflict: source changed after apply")
            return checkpoint
        if checkpoint.get("journal"):
            raise ReskinError("Interrupted apply detected; rollback this run before retrying")
        if plan["state"] != "planned":
            raise ReskinError("Plan has unresolved errors; inspect issues before applying")
        current = index_source(plan["profile"])["files"]
        if current != plan["source_fingerprint"]:
            raise ReskinError("Source conflict: source changed after planning; create a new plan")
        # Check EVERY target before any write; also catches files absent at plan time.
        for entry in plan["files"]:
            path = contained(root, entry["path"])
            actual = digest(path.read_bytes()) if path.exists() else None
            if actual != entry["before"]:
                raise ReskinError(f"Source conflict: {entry['path']}")
        checkpoint.update(state="applying", journal=[])
        write_json(run / "checkpoint.json", checkpoint)
        try:
            for entry in plan["files"]:
                # Write-ahead journal allows rollback after interruption.
                checkpoint["journal"].append(entry["path"])
                write_json(run / "checkpoint.json", checkpoint)
                atomic_write(contained(root, entry["path"]), contained(run / "staged", entry["path"]).read_bytes())
        except BaseException:
            checkpoint["state"] = "apply-interrupted"
            write_json(run / "checkpoint.json", checkpoint)
            raise
        checkpoint.update(state="patched", applied=True, source_after=index_source(plan["profile"])["files"])
        write_json(run / "checkpoint.json", checkpoint)
        return checkpoint


def rollback(run: Path):
    plan = load_plan(run)
    root = Path(plan["source"])
    with lock(root), lock(run):
        checkpoint = read_json(run / "checkpoint.json")
        paths = set(checkpoint.get("journal", []))
        if not paths:
            if checkpoint.get("applied") or checkpoint.get("state") == "rolled-back":
                checkpoint.update(state="rolled-back", applied=False, journal=[])
                write_json(run / "checkpoint.json", checkpoint)
                return checkpoint
            raise ReskinError("This run has no applied changes to roll back")
        entries = [entry for entry in plan["files"] if entry["path"] in paths]
        # Conflict-check the entire transaction before restoring anything.
        for entry in entries:
            path = contained(root, entry["path"])
            current = digest(path.read_bytes()) if path.exists() else None
            if current not in (entry["before"], entry["after"]):
                raise ReskinError(f"Rollback conflict: user changed {entry['path']}; no files restored")
        for entry in reversed(entries):
            path = contained(root, entry["path"])
            if entry["before"] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, contained(run / "backups", entry["path"]).read_bytes())
        checkpoint.update(state="rolled-back", applied=False, journal=[])
        write_json(run / "checkpoint.json", checkpoint)
        return checkpoint
