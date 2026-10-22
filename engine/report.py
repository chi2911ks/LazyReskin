from __future__ import annotations

import html
import json
from pathlib import Path

from .common import atomic_write, read_json


def render_report(run: Path) -> Path:
    plan = read_json(run / "change-plan.json")
    checkpoint = read_json(run / "checkpoint.json")
    escape = lambda value: html.escape(str(value), quote=True)
    rows = "".join(f'<tr><td>{escape(item["path"])}</td><td>{"Added" if item["before"] is None else "Modified"}</td></tr>' for item in plan["files"])
    captures = []
    for actual in sorted(run.glob("iterations/*/*/actual.png")):
        folder = actual.parent
        figures = []
        for filename in ("reference.png", "actual.png", "overlay.png", "diff.png"):
            image = folder / filename
            if image.exists():
                relative = image.relative_to(run).as_posix()
                figures.append(f'<figure><figcaption>{filename}</figcaption><a href="{escape(relative)}"><img loading="lazy" src="{escape(relative)}" alt="{escape(filename)}"></a></figure>')
        captures.append(f'<section><h2>{escape(folder.parent.name)} / iteration {escape(folder.name)}</h2><div class="images">{"".join(figures)}</div></section>')
    issues = json.dumps({"planning": plan["issues"], "validation": checkpoint.get("validation", {})}, ensure_ascii=False, indent=2)
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LazyReskin report</title>
<style>body{{font:15px/1.6 system-ui;margin:32px auto;padding:0 24px;max-width:1200px;color:#182d2b;background:#f6f8f8}}h1{{margin-bottom:8px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;border-bottom:1px solid #bbc9c5;padding:8px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;border:1px solid #bbc9c5}}.images{{display:flex;flex-wrap:wrap;gap:16px}}figure{{margin:0;width:240px}}img{{max-width:100%;border:1px solid #bbc9c5}}a{{color:#146353}}code{{overflow-wrap:anywhere}}</style>
<h1>LazyReskin: {escape(plan["skin"])}</h1><p>State: <strong>{escape(checkpoint["state"])}</strong> | Mode: {escape(plan["mode"])}</p>
<p>Source: <code>{escape(plan["source"])}</code></p><p>Runtime checks and visual checks are separate gates. A staged or patched run is not a completed reskin.</p>
<p><a href="patch.diff">Patch</a> · <a href="change-plan.json">Change plan</a> · <a href="mapping.json">Mapping</a> · <a href="checkpoint.json">Checkpoint</a></p>
<h2>Files ({len(plan["files"])})</h2><table><tr><th>Path</th><th>Change</th></tr>{rows}</table>
{"".join(captures)}<h2>Issues and validation</h2><pre>{escape(issues)}</pre></html>'''
    target = run / "report.html"
    atomic_write(target, document.encode("utf-8"))
    return target
