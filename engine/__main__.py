from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundle import Bundle
from .common import ReskinError, load_profile, read_json, write_json
from .compare import compare_images
from .planner import create_plan
from .report import render_report
from .runner import execute
from .source import index_source
from .transaction import apply_plan, lock, rollback


def parser():
    result = argparse.ArgumentParser(prog="python -m engine", description="Local Android XML colors/icons reskin workflow")
    commands = result.add_subparsers(dest="action", required=True)
    auto = commands.add_parser("auto", help="AI creates mappings/configuration and iterates in an isolated source copy")
    auto.add_argument("--source", type=Path, required=True)
    auto.add_argument("--bundle", type=Path, required=True)
    auto.add_argument("--output", type=Path, required=True)
    auto.add_argument("--dry-run", action="store_true")
    auto.add_argument("--serial")
    auto.add_argument("--screens", nargs="+", help="Optional Figma JSON filenames, otherwise process all")
    auto.add_argument("--max-iterations", type=int, default=6)
    auto.add_argument("--agent-timeout", type=int, default=600)
    auto.add_argument("--agent-command", type=Path, help="Optional trusted JSON argv adapter; defaults to installed Codex CLI")
    auto.add_argument("--pixel-tolerance", type=int, default=8)
    auto.add_argument("--max-mismatch-ratio", type=float, default=.01)
    inspect = commands.add_parser("inspect", help="Read-only bundle/source analysis")
    inspect.add_argument("--project", type=Path, required=True)
    inspect.add_argument("--bundle", type=Path, required=True)
    inspect.add_argument("--output", type=Path)
    for action in ("plan", "run"):
        command = commands.add_parser(action, help="Stage a dry-run" if action == "plan" else "Plan, baseline, apply and validate")
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--skin", type=Path, required=True)
        command.add_argument("--run-dir", type=Path, required=True)
        command.add_argument("--screens", nargs="+")
    for action in ("apply", "resume", "report", "rollback"):
        command = commands.add_parser(action)
        command.add_argument("--run", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--actual", type=Path, required=True)
    compare.add_argument("--config", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "auto":
            from .auto import automatic
            if not 0 <= args.pixel_tolerance <= 255 or not 0 <= args.max_mismatch_ratio <= 1:
                raise ReskinError("Invalid automatic comparison policy")
            result = automatic(args.source, args.bundle, args.output,
                               agent_command=read_json(args.agent_command) if args.agent_command else None,
                               max_iterations=args.max_iterations, dry_run=args.dry_run, serial=args.serial,
                               screen_names=args.screens, agent_timeout=args.agent_timeout,
                               policy={"pixel_tolerance": args.pixel_tolerance, "max_mismatch_ratio": args.max_mismatch_ratio})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["state"] in ("planned", "completed") else 2
        if args.action == "inspect":
            profile = load_profile(args.project.resolve())
            if args.output and args.output.resolve().is_relative_to(Path(profile["source"])):
                raise ReskinError("Inspect output must be outside the Android source directory")
            with Bundle(args.bundle) as bundle:
                source = index_source(profile)
                result = {"bundle": bundle.summary(), "source": source}
            if args.output:
                write_json(args.output, result)
            summary = {"layouts": len(source["layouts"]), "runtime_overrides": len(source["runtime_overrides"]), "screens": len(result["bundle"]["screens"]), "assets": result["bundle"]["assets"], "diagnostics": result["bundle"]["diagnostics"] + source["diagnostics"], "output": str(args.output) if args.output else None}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.action in ("plan", "run"):
            with lock(args.run_dir.resolve()), Bundle(args.bundle) as bundle:
                plan = create_plan(load_profile(args.project.resolve()), read_json(args.skin), bundle, args.run_dir.resolve(), args.screens)
            render_report(args.run_dir.resolve())
            result = {"state": plan["state"], "files": len(plan["files"]), "issues": plan["issues"], "report": str((args.run_dir / "report.html").resolve())}
            if args.action == "run" and plan["state"] == "planned":
                result = execute(args.run_dir.resolve())
        elif args.action == "compare":
            result = compare_images(args.reference, args.actual, args.output, read_json(args.config))
        elif args.action == "report":
            result = {"report": str(render_report(args.run.resolve()))}
        elif args.action == "apply":
            result = apply_plan(args.run.resolve())
            render_report(args.run.resolve())
        elif args.action == "rollback":
            result = rollback(args.run.resolve())
            render_report(args.run.resolve())
        else:
            result = execute(args.run.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("state") in (None, "planned", "patched", "rolled-back", "completed", "visual-passed") else 2
    except (ReskinError, OSError, KeyError, TypeError, ValueError) as exc:
        run = getattr(args, "run", None) or getattr(args, "run_dir", None)
        if run and (run / "checkpoint.json").is_file():
            try:
                checkpoint = read_json(run / "checkpoint.json")
                checkpoint["last_error"] = {"action": args.action, "message": str(exc)}
                # Keep rollback/apply journal states for recovery, rather than
                # describing an interrupted transaction as a fresh validation.
                if args.action != "rollback" and checkpoint.get("state") != "apply-interrupted":
                    checkpoint["state"] = "source-conflict" if "Source" in str(exc) else "needs-input"
                write_json(run / "checkpoint.json", checkpoint)
                render_report(run)
            except (ReskinError, OSError, ValueError, KeyError, TypeError):
                pass
        print(f"reskin: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
