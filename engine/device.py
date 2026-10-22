"""Standalone ADB capture adapter; logs stderr and writes only PNG to stdout."""
import argparse
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def resolve_apk(root: Path, variant: str):
    root = root.resolve()
    normalize = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
    choices = []
    for metadata_path in root.rglob("output-metadata.json"):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        if normalize(metadata.get("variantName", "")) != normalize(variant):
            continue
        elements = metadata.get("elements", [])
        universal = [element for element in elements if not element.get("filters")]
        if len(universal) != 1:
            continue
        apk = (metadata_path.parent / universal[0]["outputFile"]).resolve()
        if not apk.is_relative_to(root) or not apk.is_file():
            raise RuntimeError("APK metadata points outside output directory or to a missing file")
        application_id = metadata.get("applicationId", "")
        if not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", application_id):
            raise RuntimeError("APK metadata has no valid applicationId")
        choices.append((apk, application_id))
    if len(choices) != 1:
        raise RuntimeError(f"Expected one universal APK for variant {variant}, found {len(choices)}")
    return choices[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial")
    parser.add_argument("--apk")
    parser.add_argument("--apk-root", type=Path)
    parser.add_argument("--variant", default="debug")
    parser.add_argument("--check-only", action="store_true", help="Verify ready state without writing screenshot bytes")
    parser.add_argument("--activity", required=True)
    parser.add_argument("--wait-id", help="Full resource ID visible before capture")
    parser.add_argument("--wait-text")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not args.wait_id and not args.wait_text:
        parser.error("Specify --wait-id or --wait-text; capture needs an observable ready state")
    if not 1 <= args.timeout <= 180:
        parser.error("--timeout must be 1..180")
    prefix = [args.adb] + (["-s", args.serial] if args.serial else [])

    def adb(arguments, timeout=30):
        result = subprocess.run(prefix + arguments, capture_output=True, timeout=timeout, shell=False)
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace") or result.stdout.decode("utf-8", errors="replace"))
        return result.stdout

    try:
        application_id = None
        if args.apk_root:
            apk, application_id = resolve_apk(args.apk_root, args.variant)
            args.apk = str(apk)
        activity = args.activity
        if "/" not in activity:
            if not application_id:
                raise RuntimeError("Class-only activity needs APK metadata (--apk-root)")
            activity = application_id + "/" + activity
        if args.wait_id and ":id/" not in args.wait_id:
            application_id = application_id or activity.split("/", 1)[0]
            args.wait_id = application_id + ":id/" + args.wait_id
        if args.apk:
            print(adb(["install", "-r", args.apk], timeout=120).decode(errors="replace"), file=sys.stderr)
        print(adb(["shell", "am", "start", "-W", "-n", activity]).decode(errors="replace"), file=sys.stderr)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            remaining = max(1, min(15, deadline - time.monotonic()))
            adb(["shell", "uiautomator", "dump", "/sdcard/lazyreskin-window.xml"], timeout=remaining)
            raw = adb(["exec-out", "cat", "/sdcard/lazyreskin-window.xml"], timeout=remaining)
            document = ET.fromstring(raw)
            matched = any((not args.wait_id or node.get("resource-id") == args.wait_id)
                          and (not args.wait_text or node.get("text") == args.wait_text)
                          and node.get("visible-to-user", "true") == "true" for node in document.iter("node"))
            if matched:
                if args.check_only:
                    print("Activity ready-state smoke check passed")
                    return 0
                screenshot = adb(["exec-out", "screencap", "-p"])
                if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError("ADB did not return PNG")
                sys.stdout.buffer.write(screenshot)
                return 0
            time.sleep(.5)
        raise RuntimeError("Ready-state timeout; screenshot was not taken")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        print(f"ADB capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
