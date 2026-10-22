---
name: xml-reskin
description: Automatically reskin colors and icons in an existing Android XML app from source and a Figma bundle using LazyReskin auto, AI-generated mappings, staged patches, capture and comparison. New app generation and layout redesign require a separate scope.
---

# Android XML reskin

Use the local engine from this repository root. Read [the CLI guide](../../docs/reskin-engine.md) for configuration, commands and supported mappings.

## Automatic route

When the user asks for AI to handle the whole job, do not ask them to fill profile/skin JSON. Use the source and Figma bundle paths already available, and read [automatic reskin](../../docs/automatic-reskin.md).

`python -m engine auto --source <source> --bundle <bundle> --output <new-run-directory>` creates an isolated source copy and uses a read-only Codex proposal agent by default. The engine validates proposals and applies/builds/captures color/icon patches. `--dry-run` creates mappings and staged patches without an Android device.

If a bundle/source path is actually missing, request only that input. If the runtime device, CLI login or capture environment is unavailable, finish the independent planning work and report the concrete blocker. Do not substitute fake smoke checks or claim a complete reskin from stub tests.

The automatic route invokes a proposal agent as part of this workflow; do not launch additional agents or use approval/sandbox bypass flags. Source copied by auto is the deliverable workspace; never automatically merge it over the original source.

## Working procedure

1. Identify the existing app source, selected module/variant, Figma bundle and intended screens. Default to `colors-icons`. Preserve uncommitted work; use an isolated working copy/worktree when appropriate.
2. Run `python -m engine inspect` before editing. Read source XML, resource consumers and Kotlin/Java styling around target views. Name-match suggestions are hints, never confirmed mappings.
3. Create a project profile and explicit skin mappings. Keep view IDs, types, listeners and business behavior. Read `screens/*.json`, reference screenshots and asset manifest together. Do not use unrelated icons or silently flatten unsupported SVG features.
4. Run `plan`. Read `change-plan.json`, `mapping.json`, `patch.diff` and report. Resolve mapping errors and runtime styling conflicts. `runtime_reviewed: true` records an actual source review; it is not a shortcut around unknown behavior.
5. Run build/capture/compare with `run`, or use `apply` explicitly when only a staged, unverified patch is authorized. A `patched` or `visual-passed-unverified` state is not completed reskin.
6. Read region issues, actual/reference/overlay/diff images and runtime evidence. For a failed candidate, change only the mapped properties that explain the error. Make a new plan/run against the current source with an updated skin; preserve the previous run's backup and report. Roll back child runs before their parent if they touch the same files.
7. Keep the best candidate. Bound the whole agent-led job to the configured iteration budget (default six candidates per screen); stop after two candidates without improvement, missing required data or unresolved source conflicts. Engine `resume` retries validation on an unchanged candidate; it does not invoke an AI repair agent.
8. Run the declared smoke/regression commands. If functional checks, a device or reference screenshots are missing, report the exact unverified part instead of declaring full completion.

## Scope and evidence

- This engine release patches color and image attributes, not arbitrary layout structure, gradients or typography. Report changes outside that scope rather than creating new screens or refactoring the app.
- Screenshots need matching viewport, density, font scale, locale, theme, content and state. Do not resize images or broaden masks to hide discrepancies.
- Masks need a specific dynamic-region reason. Inspect critical regions separately; a good full-screen score can hide a wrong small icon.
- Source fingerprint mismatches require a new plan. Do not overwrite a user's changes to make an old run resume.
- The engine creates scoped resources to avoid changing unrelated consumers. Still inspect include/template consumers and use the appropriate regression checks.
- Installation of this portable skill into an agent's skill root is a separate task; its presence here does not register it globally.
