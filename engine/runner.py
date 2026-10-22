from __future__ import annotations

import subprocess
from pathlib import Path

from .assets import png_dimensions
from .common import ReskinError, atomic_write, contained, digest, read_json, slug, write_json
from .compare import compare_images
from .report import render_report
from .source import index_source
from .transaction import apply_plan, load_plan, lock


def command(argv, source: Path, run: Path, output: Path, timeout=180, binary=False):
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise ReskinError("Commands must be non-empty arrays of strings; shell strings are not supported")
    expanded = [arg.replace("{source}", str(source)).replace("{run}", str(run)).replace("{output}", str(output)).replace("{engine}", str(Path(__file__).resolve().parent.parent)) for arg in argv]
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 900:
        raise ReskinError("Command timeout must be 1..900 seconds")
    try:
        process = subprocess.run(expanded, cwd=source, capture_output=True, timeout=timeout, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        atomic_write(output.with_suffix(".error.log"), str(exc).encode())
        return {"passed": False, "command": expanded, "error": str(exc)}
    if binary:
        if process.returncode == 0:
            try:
                png_dimensions(process.stdout)
            except ReskinError as exc:
                atomic_write(output.with_suffix(".error.log"), str(exc).encode())
                return {"passed": False, "command": expanded, "error": str(exc)}
            atomic_write(output, process.stdout)
        atomic_write(output.with_suffix(".stderr.log"), process.stderr)
    else:
        atomic_write(output, process.stdout + b"\n" + process.stderr)
    return {"passed": process.returncode == 0, "command": expanded, "exit_code": process.returncode, "log": str(output)}


def capture(target, profile, source, run, folder):
    config = dict(profile.get("screens", {}).get(target["key"], {}).get("capture", {}))
    config.update(target.get("capture", {}))
    if not config.get("command"):
        return {"passed": False, "error": "capture-command-required", "screen": target["key"]}
    folder.mkdir(parents=True, exist_ok=True)
    for number, step in enumerate(config.get("steps", [])):
        result = command(step, source, run, folder / f"step-{number}.log", config.get("timeout", 180))
        if not result["passed"]:
            return result
    return command(config["command"], source, run, folder / "actual.png", config.get("timeout", 180), binary=True)


def execute(run: Path):
    plan = load_plan(run)
    profile, source = plan["profile"], Path(plan["source"])
    checkpoint = read_json(run / "checkpoint.json")
    if plan["state"] != "planned":
        raise ReskinError("Unresolved plan; cannot execute")
    if checkpoint["state"] == "rolled-back":
        raise ReskinError("Rolled-back runs cannot resume; create a new plan")
    if checkpoint["state"] in ("iteration-limit", "iteration-stalled"):
        return checkpoint
    maximum = profile.get("max_iterations", 6)
    if not isinstance(maximum, int) or not 1 <= maximum <= 30:
        raise ReskinError("max_iterations must be an integer in 1..30")
    build = profile.get("build", {})
    if not build.get("command"):
        checkpoint.update(state="needs-input", validation={"error": "build-command-required; use apply explicitly for an unverified patch"})
        write_json(run / "checkpoint.json", checkpoint)
        render_report(run)
        return checkpoint
    if not checkpoint.get("applied"):
        # Preflight command execution is serialized for this source.
        with lock(source), lock(run):
            if index_source(profile)["files"] != plan["source_fingerprint"]:
                raise ReskinError("Source conflict before baseline build")
            baseline = command(build["command"], source, run, run / "baseline" / "build.log", build.get("timeout", 180))
            if not baseline["passed"]:
                checkpoint.update(state="baseline-build-failed", validation=baseline)
                write_json(run / "checkpoint.json", checkpoint)
                render_report(run)
                return checkpoint
            for target in plan["targets"]:
                result = capture(target, profile, source, run, run / "baseline" / slug(target["key"]))
                if not result["passed"]:
                    checkpoint.update(state="baseline-capture-failed", validation=result)
                    write_json(run / "checkpoint.json", checkpoint)
                    render_report(run)
                    return checkpoint
        checkpoint = apply_plan(run)
    with lock(source), lock(run):
        checkpoint = read_json(run / "checkpoint.json")
        if index_source(profile)["files"] != checkpoint.get("source_after"):
            raise ReskinError("Source changed after apply; replan instead of replaying an old checkpoint")
        iteration = checkpoint.get("iteration", 0) + 1
        if iteration > maximum:
            checkpoint["state"] = "iteration-limit"
            write_json(run / "checkpoint.json", checkpoint)
            render_report(run)
            return checkpoint
        checkpoint.update(state="building", iteration=iteration)
        write_json(run / "checkpoint.json", checkpoint)
        build_result = command(build["command"], source, run, run / "iterations" / f"build-{iteration}.log", build.get("timeout", 180))
        validation = {"build": build_result, "screens": [], "smoke": [], "regression": []}
        state = "build-failed"
        source_intact = index_source(profile)["files"] == checkpoint["source_after"]
        if not source_intact:
            state = "source-conflict"
            validation["error"] = "Build command modified indexed source; replan before continuing"
        if build_result["passed"] and source_intact:
            state = "visual-passed"
            for target in plan["targets"]:
                folder = run / "iterations" / slug(target["key"]) / str(iteration)
                checkpoint["state"] = "capturing"
                write_json(run / "checkpoint.json", checkpoint)
                result = capture(target, profile, source, run, folder)
                screen_result = {"screen": target["key"], "capture": result}
                if not result["passed"]:
                    state = "capture-failed"
                elif not target["reference"]:
                    screen_result["comparison"] = {"state": "needs-input", "issues": [{"code": "missing-reference"}]}
                    if state == "visual-passed":
                        state = "needs-input"
                else:
                    reference = contained(run, target["reference"])
                    atomic_write(folder / "reference.png", reference.read_bytes())
                    config = dict(profile.get("screens", {}).get(target["key"], {}).get("comparison", {}))
                    config.update(target["comparison"])
                    try:
                        comparison = compare_images(reference, folder / "actual.png", folder, config)
                    except ReskinError as exc:
                        comparison = {"state": "needs-input", "issues": [{"code": "comparison-config-invalid", "message": str(exc)}]}
                        write_json(folder / "issues.json", comparison)
                    screen_result["comparison"] = comparison
                    if comparison["state"] != "visual-passed" and state == "visual-passed":
                        state = comparison["state"]
                validation["screens"].append(screen_result)
            if state == "visual-passed":
                checks = profile.get("smoke", [])
                state = "completed" if checks else "visual-passed-unverified"
                for category in ("smoke", "regression"):
                    for number, check in enumerate(profile.get(category, [])):
                        result = command(check, source, run, run / "iterations" / f"{category}-{iteration}-{number}.log")
                        validation[category].append(result)
                        if not result["passed"]:
                            state = category + "-failed"
        if index_source(profile)["files"] != checkpoint["source_after"]:
            state = "source-conflict"
            validation["error"] = "Validation commands modified indexed source; screenshots cannot certify the changed candidate"
        checkpoint.update(state=state, validation=validation)
        # Immutable source evidence prevents treating a different candidate as this result.
        candidate = {"iteration": iteration, "state": state, "source_fingerprint": index_source(profile)["files"], "validation": validation}
        write_json(run / "iterations" / f"candidate-{iteration}.json", candidate)
        scores = [region["mismatch_ratio"] for screen in validation["screens"] for region in screen.get("comparison", {}).get("regions", [])]
        candidate["score"] = sum(scores) / len(scores) if scores else None
        best_path = run / "best-candidate.json"
        best = read_json(best_path) if best_path.exists() else None
        if candidate["score"] is not None and (best is None or best.get("score") is None or candidate["score"] < best["score"]):
            write_json(best_path, candidate)
            checkpoint["no_improvement"] = 0
        elif candidate["score"] is not None and state not in ("completed", "visual-passed-unverified"):
            checkpoint["no_improvement"] = checkpoint.get("no_improvement", 0) + 1
            if checkpoint["no_improvement"] >= 2:
                checkpoint["state"] = "iteration-stalled"
        write_json(run / "checkpoint.json", checkpoint)
        render_report(run)
        return checkpoint
