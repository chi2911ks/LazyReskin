from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .agent import AgentError, propose
from .bundle import Bundle
from .common import ReskinError, atomic_write, contained, digest, read_json, slug, write_json
from .planner import create_plan
from .report import render_report
from .runner import execute
from .source import index_source, parse_xml
from .transaction import apply_plan, lock, rollback


def discover(source: Path):
    modules = []
    for path in source.rglob("AndroidManifest.xml"):
        if path.parent.name != "main" or path.parent.parent.name != "src" or any(part in ("build", ".gradle", ".git", "node_modules") for part in path.relative_to(source).parts):
            continue
        module = path.parent.parent.parent.relative_to(source).as_posix()
        if module == ".":
            continue
        manifest = parse_xml(path.read_bytes(), str(path))
        android_name = "{http://schemas.android.com/apk/res/android}name"
        package = manifest.get("package", "")
        build_text = "\n".join(p.read_text(encoding="utf-8-sig") for p in path.parent.parent.parent.glob("build.gradle*") if p.is_file())
        namespace = re.search(r'namespace\s*(?:=\s*|\(\s*)["\']([^"\']+)', build_text)
        package = namespace.group(1) if namespace else package
        activities = []
        for activity in manifest.iter("activity"):
            name = activity.get(android_name, "")
            if name.startswith("."):
                name = package + name
            elif "." not in name and package:
                name = package + "." + name
            if re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", name):
                activities.append(name)
        roots = sorted(p.relative_to(path.parent.parent.parent).as_posix() for p in (path.parent.parent).glob("*/res") if p.is_dir())
        modules.append({"module": module, "resource_roots": roots, "activities": activities, "namespace": package})
    if not modules:
        raise ReskinError("No Android module with src/main/AndroidManifest.xml found")
    return modules


def snapshot(source: Path, workspace: Path):
    def ignore(directory, names):
        relative = Path(directory).relative_to(source)
        excluded = []
        for name in names:
            path = Path(directory) / name
            if name in (".git", ".gradle", ".idea", ".lazyreskin", "node_modules", "__pycache__") or (name == "build" and len(relative.parts) <= 1):
                excluded.append(name)
            elif path.is_symlink():
                raise ReskinError(f"Source snapshot cannot follow symlink: {path}")
        return excluded
    shutil.copytree(source, workspace, ignore=ignore)


def compile_proposal(proposal, modules, source, screen_file, screen_key, policy, serial=None):
    module = next((item for item in modules if item["module"] == proposal["module"]), None)
    if module is None:
        raise ReskinError("Agent selected an unknown module")
    layout = contained(source, proposal["layout"])
    profile = {"source": str(source), "module": module["module"], "resource_roots": module["resource_roots"]}
    index = index_source(profile)
    if proposal["layout"] not in index["layouts"]:
        raise ReskinError("Agent selected a layout not present in source")
    if not re.fullmatch(r"assemble[A-Za-z0-9_]+", proposal["build_task"]):
        raise ReskinError("Agent may only select an assemble task, not an arbitrary command")
    wrapper = source / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not wrapper.is_file():
        raise ReskinError(f"Gradle wrapper missing: {wrapper.name}")
    task_module = ":" + module["module"].replace("/", ":")
    profile["build"] = {"command": [str(wrapper), task_module + ":" + proposal["build_task"]], "timeout": 300}
    capture = None
    if proposal["activity"]:
        if proposal["activity"] not in module["activities"]:
            raise ReskinError("Agent capture activity must be declared in the selected module manifest")
        ids = {element["view"] for element in index["layouts"][proposal["layout"]] if element["view"]}
        wait_id = proposal["wait_id"]
        if wait_id not in ids:
            raise ReskinError("Agent must choose a ready view ID present in the mapped layout")
        capture = [sys.executable, "{engine}/engine/device.py", "--apk-root", str(contained(source, module["module"]) / "build/outputs/apk"),
                   "--variant", proposal["build_task"][len("assemble"):], "--activity", proposal["activity"], "--wait-id", wait_id]
        if serial:
            capture += ["--serial", serial]
        if proposal["wait_text"]:
            capture += ["--wait-text", proposal["wait_text"]]
        profile["screens"] = {screen_key: {"capture": {"command": capture, "timeout": 180}, "comparison": dict(policy)}}
        profile["smoke"] = [capture + ["--check-only"]]
    grouped = {}
    for item in proposal["mappings"]:
        if item["kind"] in ("asset", "value"):
            value = {item["kind"]: item["value"]}
        else:
            value = item["kind"]
        key = item["node"], item["view"]
        mapping = grouped.setdefault(key, {"node": item["node"], "view": item["view"], "properties": {}})
        if item["attribute"] in mapping["properties"]:
            raise ReskinError("Agent supplied duplicate/conflicting properties")
        mapping["properties"][item["attribute"]] = value
    if not grouped:
        raise ReskinError("Agent supplied no mappings")
    skin = {"name": "auto", "mode": "colors-icons", "screens": [{"key": screen_key, "figma": screen_file, "layout": proposal["layout"], "mappings": list(grouped.values())}]}
    return profile, skin, index, bool(capture)


def auto_report(job: Path, state):
    rows = []
    for item in state.get("screens", []):
        candidate = item.get("candidate")
        link = f'<a href="{html.escape(candidate, quote=True)}/report.html">Candidate report</a>' if candidate else ""
        rows.append(f'<tr><td>{html.escape(item["screen"])}</td><td>{html.escape(item["state"])}</td><td>{html.escape(item.get("reason", ""))}</td><td>{link}</td></tr>')
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LazyReskin auto</title><style>body{{font:15px/1.6 system-ui;max-width:1100px;margin:32px auto;padding:0 24px;background:#f6f8f8;color:#182d2b}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #bbc9c5;overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse}}a{{color:#146353}}</style><h1>LazyReskin automatic reskin</h1><p>State: <strong>{html.escape(state["state"])}</strong></p><p>Working source: {html.escape(state["workspace"])}</p><p>Original source is not patched. AI produces proposals; the engine applies and validates scoped changes.</p><table><tr><th>Figma screen</th><th>Result</th><th>Details</th><th>Evidence</th></tr>{''.join(rows)}</table></html>'''
    atomic_write(job / "report.html", document.encode())
    write_json(job / "auto-checkpoint.json", state)


def device_ready(serial=None):
    if not shutil.which("adb"):
        raise ReskinError("ADB is not available; install Android platform tools or use --dry-run")
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, timeout=20, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReskinError(f"Cannot query ADB: {exc}") from exc
    connected = [line.split()[0] for line in result.stdout.decode(errors="replace").splitlines()[1:] if len(line.split()) >= 2 and line.split()[1] == "device"]
    if result.returncode or (serial and serial not in connected) or (not serial and len(connected) != 1):
        raise ReskinError("Auto execution needs one connected/authorized Android device, or --serial; use --dry-run to generate mappings without a device")


def automatic(source: Path, bundle_path: Path, job: Path, *, agent_command=None, max_iterations=6,
              dry_run=False, serial=None, screen_names=None, policy=None, agent_timeout=600):
    source, job = source.resolve(), job.resolve()
    if not source.is_dir():
        raise ReskinError("Auto source must be an existing directory")
    if job.is_relative_to(source):
        raise ReskinError("Auto output must be outside original source")
    if not isinstance(max_iterations, int) or not 1 <= max_iterations <= 12:
        raise ReskinError("Auto max_iterations must be 1..12 per screen")
    if job.exists() and any(job.iterdir()):
        raise ReskinError("Auto output must be a new/empty directory")
    if not dry_run:
        device_ready(serial)
    if not isinstance(agent_timeout, int) or not 1 <= agent_timeout <= 1800:
        raise ReskinError("Agent timeout must be 1..1800 seconds")
    policy = policy or {"pixel_tolerance": 8, "max_mismatch_ratio": .01}
    with lock(job), Bundle(bundle_path) as bundle:
        # Validate inputs before copying or invoking a provider.
        selected = [name for name in bundle.screens if not screen_names or name in screen_names or Path(name).name in screen_names]
        if not selected or (screen_names and len(selected) != len(set(screen_names))):
            raise ReskinError("Requested auto screen files were not found uniquely in the bundle")
        workspace = job / "workspace"
        job.mkdir(parents=True, exist_ok=True)
        snapshot(source, workspace)
        modules = discover(workspace)
        original_modules = discover(source)
        original_fingerprints = {item["module"]: index_source({"source": str(source), **item})["files"] for item in original_modules}
        context = job / "context"
        for name in bundle.files:
            atomic_write(contained(context / "bundle", name), bundle.read(name))
        # Plan against the same copied input that the agent reads, not a ZIP
        # that could be replaced by the caller halfway through a job.
        bundle = Bundle(context / "bundle")
        write_json(context / "modules.json", modules)
        state = {"state": "planning", "original": str(source), "workspace": str(workspace), "screens": [], "policy": policy,
                 "max_iterations": max_iterations, "agent": "custom-command" if agent_command else "codex-read-only"}
        auto_report(job, state)
        abort_job = False
        accepted_properties = {}
        try:
            for screen_file in selected:
                key = slug(Path(screen_file).stem) + "_" + digest(screen_file.encode())[:6]
                feedback, best, stagnant = {}, None, 0
                result = {"screen": screen_file, "state": "needs-input", "reason": ""}
                state["screens"].append(result)
                for iteration in range(1, max_iterations + 1):
                    result["attempts"] = iteration
                    print(f"[auto] {screen_file}: proposal {iteration}/{max_iterations}", file=sys.stderr, flush=True)
                    indexes = []
                    for module in modules:
                        index = index_source({"source": str(workspace), **module})
                        index_path = context / (slug(module["module"]) + "-source-index.json")
                        write_json(index_path, index)
                        indexes.append(str(index_path))
                    before = {item["module"]: index_source({"source": str(workspace), **item})["files"] for item in modules}
                    reference = bundle.reference(screen_file)
                    images = [str(context / "bundle" / reference)] if reference else []
                    images += feedback.get("images", [])
                    folder = job / "proposals" / key / str(iteration)
                    request = {"job_root": str(job), "source": str(workspace), "modules": modules, "source_indexes": indexes,
                               "figma": str(context / "bundle" / screen_file), "asset_manifest": str(context / "bundle/assets.manifest.json"),
                               "screen": screen_file, "images": images, "feedback": feedback, "scope": "colors-icons"}
                    try:
                        proposal = propose(request, folder, agent_command, agent_timeout)
                        after = {item["module"]: index_source({"source": str(workspace), **item})["files"] for item in modules}
                        if before != after:
                            raise ReskinError("AI provider modified source; automatic patching stopped")
                        if proposal["status"] == "blocked":
                            result.update(state="needs-input", reason=proposal["reason"])
                            break
                        profile, skin, _, has_capture = compile_proposal(proposal, modules, workspace, screen_file, key, policy, serial)
                        write_json(folder / "project.json", profile)
                        write_json(folder / "skin.json", skin)
                        candidate = job / "candidates" / key / str(iteration)
                        plan = create_plan(profile, skin, bundle, candidate)
                        render_report(candidate)
                        result.update(candidate=candidate.relative_to(job).as_posix(), reason=proposal["reason"])
                        if plan["state"] != "planned":
                            feedback = {"planning_issues": plan["issues"], "previous_proposal": proposal}
                            result["state"] = "needs-input"
                            continue
                        conflicts = [operation for operation in plan["operations"]
                                     if (operation["layout"], operation["view"], operation["attribute"]) in accepted_properties
                                     and accepted_properties[(operation["layout"], operation["view"], operation["attribute"])] != operation["after"]]
                        if conflicts:
                            result.update(state="needs-input", reason="Different Figma states target the same view property differently; selector/state mapping is required")
                            write_json(candidate / "cross-screen-conflicts.json", conflicts)
                            break
                        if dry_run:
                            result["state"] = "planned"
                            break
                        if not has_capture:
                            result.update(state="needs-input", reason="Agent could not establish an Activity and observable capture state")
                            break
                        validation = execute(candidate)
                        result["state"] = validation["state"]
                        if validation["state"] == "completed":
                            break
                        scores = [region["mismatch_ratio"] for item in validation.get("validation", {}).get("screens", []) for region in item.get("comparison", {}).get("regions", [])]
                        score = sum(scores) / len(scores) if scores else None
                        feedback = {"previous_proposal": proposal, "validation": validation, "images": [str(p) for p in candidate.glob("iterations/*/*/*.png") if p.name in ("actual.png", "diff.png")]}
                        if validation.get("applied"):
                            rollback(candidate)
                            if score is not None and (best is None or score < best["score"]):
                                best = {"candidate": candidate, "score": score, "validation": validation}
                                stagnant = 0
                            else:
                                stagnant += 1
                        if validation["state"] == "source-conflict":
                            raise ReskinError("Validation modified source unexpectedly; automatic repairs stopped")
                        if stagnant >= 2:
                            result["state"] = "iteration-stalled"
                            break
                        auto_report(job, state)
                    except ReskinError as exc:
                        result.update(state="needs-input", reason=str(exc))
                        # Protocol/availability/conflict failures should not consume repeated model calls.
                        if isinstance(exc, AgentError) or "provider modified" in str(exc) or "conflict" in str(exc).lower():
                            abort_job = True
                            break
                        feedback = {"error": str(exc), "previous_feedback": feedback}
                if not abort_job and result["state"] not in ("completed", "planned") and best:
                    apply_plan(best["candidate"])
                    checkpoint = read_json(best["candidate"] / "checkpoint.json")
                    checkpoint.update(state=best["validation"]["state"], validation=best["validation"].get("validation", {}))
                    write_json(best["candidate"] / "checkpoint.json", checkpoint)
                    render_report(best["candidate"])
                    result.update(candidate=best["candidate"].relative_to(job).as_posix(), best_score=best["score"])
                if result.get("candidate") and (result["state"] == "completed" or (best and not abort_job)):
                    kept_plan = read_json(job / result["candidate"] / "change-plan.json")
                    for operation in kept_plan["operations"]:
                        accepted_properties[(operation["layout"], operation["view"], operation["attribute"])] = operation["after"]
                auto_report(job, state)
                if abort_job:
                    break
            processed = {item["screen"] for item in state["screens"]}
            for filename in selected:
                if filename not in processed:
                    state["screens"].append({"screen": filename, "state": "not-run", "reason": "Job stopped after provider/protocol or source-integrity failure"})
            if not dry_run and not abort_job:
                # Validate previously accepted screens against the final source,
                # because later XML/include changes can affect earlier screens.
                for item in state["screens"]:
                    if item["state"] != "completed":
                        continue
                    previous = job / item["candidate"]
                    inputs = read_json(previous / "inputs.json")
                    final = job / "final-validation" / slug(previous.parent.name)
                    final_plan = create_plan(inputs["profile"], inputs["skin"], bundle, final)
                    render_report(final)
                    if final_plan["state"] != "planned" or final_plan["files"]:
                        item.update(state="regression-failed", reason="Later candidates changed an accepted target; final verification requires review")
                    else:
                        verified = execute(final)
                        item["final_validation"] = final.relative_to(job).as_posix()
                        if verified["state"] != "completed":
                            item.update(state="regression-failed", reason="Final-source visual/runtime verification did not pass")
            states = {item["state"] for item in state["screens"]}
            state["state"] = "planned" if dry_run and states == {"planned"} else "completed" if states == {"completed"} else "partial"
        finally:
            current_original = {item["module"]: index_source({"source": str(source), **item})["files"] for item in original_modules}
            if current_original != original_fingerprints:
                state["state"] = "original-source-conflict"
                auto_report(job, state)
                raise ReskinError("Original source changed while auto ran; no automatic merge will be performed")
            auto_report(job, state)
        return state
