from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from engine.assets import figma_color, svg_to_vector
from engine.bundle import Bundle
from engine.common import ReskinError, digest, read_json, write_json
from engine.compare import compare_images
from engine.planner import create_plan
from engine.report import render_report
from engine.runner import execute
from engine.source import affected_consumers, index_source
from engine.transaction import apply_plan, rollback
from engine.xmlpatch import patch_attributes


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lazyreskin-test-")
        self.base = Path(self.temporary.name)
        self.source = self.base / "android"
        self.res = self.source / "app/src/main/res"
        for directory in ("layout", "values", "drawable"):
            (self.res / directory).mkdir(parents=True, exist_ok=True)
        self.layout = "app/src/main/res/layout/home.xml"
        self.original = b'''<?xml version="1.0" encoding="utf-8"?>\r\n<!-- Keep this comment and whitespace. -->\r\n<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" android:id="@+id/root" android:layout_width="match_parent" android:layout_height="match_parent">\r\n    <TextView android:id="@+id/title" android:text="Keep &amp; this" android:textColor="@color/brand" />\r\n    <ImageView android:id="@+id/icon" android:src="@drawable/old_icon" />\r\n</LinearLayout>\r\n'''
        (self.source / self.layout).write_bytes(self.original)
        (self.res / "values/colors.xml").write_text('<resources><color name="brand">#112233</color></resources>')
        (self.res / "layout/other.xml").write_text('<View xmlns:android="http://schemas.android.com/apk/res/android" android:background="@color/brand"/>')
        (self.res / "drawable/old_icon.xml").write_text('<shape/>')
        (self.source / "build.gradle.kts").write_text('// Existing build config\n')
        self.bundle_dir = self.base / "bundle"
        for directory in ("screens", "screenshots", "assets"):
            (self.bundle_dir / directory).mkdir(parents=True)
        self.node = {"id": "1:2", "name": "Title", "type": "TEXT", "visible": True, "opacity": 1, "text": {"characters": "Keep & this"}, "fills": [{"type": "SOLID", "color": {"r": .2, "g": .4, "b": .6}, "opacity": 1}], "parentChain": [{"id": "1:1"}]}
        self.screen = {"root": {"id": "1:1", "name": "Home"}, "flattened": [{"id": "1:1", "name": "Home", "type": "FRAME", "childIds": ["1:2"]}, self.node]}
        write_json(self.bundle_dir / "screens/home.json", self.screen)
        Image.new("RGB", (16, 16), "#336699").save(self.bundle_dir / "screenshots/home.png")
        Image.new("RGBA", (8, 8), "#ff0000").save(self.bundle_dir / "assets/icon.png")
        write_json(self.bundle_dir / "meta.json", {"pngScale": 2})
        write_json(self.bundle_dir / "assets.manifest.json", {"schemaVersion": 2, "assets": [{"nodeId": "asset:1", "filename": "icon.png", "status": "exported", "scale": 2}]})
        self.profile = {"source": str(self.source), "module": "app", "resource_roots": ["src/main/res"]}
        self.skin = {"name": "Forest", "mode": "colors-icons", "screens": [{"key": "home", "layout": self.layout, "figma": "home.json", "mappings": [{"node": "1:2", "view": "title", "properties": {"android:textColor": "textColor"}}, {"node": "1:2", "view": "icon", "properties": {"android:src": {"asset": "icon.png"}}}], "comparison": {"pixel_tolerance": 0, "max_mismatch_ratio": 0}}]}
        self.run = self.base / "run"

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self, directory=None):
        with Bundle(self.bundle_dir) as bundle:
            return create_plan(self.profile, self.skin, bundle, directory or self.run)

    def configure_commands(self, capture_bytes=None):
        self.profile["build"] = {"command": [sys.executable, "-c", "print('fixture build command')"]}
        if capture_bytes is None:
            capture_bytes = (self.bundle_dir / "screenshots/home.png").read_bytes()
        self.profile["screens"] = {"home": {"capture": {"command": [sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes.fromhex('" + capture_bytes.hex() + "'))"]}}}

    def test_plan_is_read_only_and_scopes_color_without_changing_shared_resource(self):
        before = index_source(self.profile)["files"]
        plan = self.plan()
        self.assertEqual(plan["state"], "planned")
        self.assertEqual(index_source(self.profile)["files"], before)
        apply_plan(self.run)
        content = (self.source / self.layout).read_bytes()
        self.assertIn(b'<!-- Keep this comment and whitespace. -->\r\n', content)
        self.assertIn(b'android:text="Keep &amp; this"', content)
        self.assertIn(b'android:id="@+id/title"', content)
        self.assertIn(b'@color/lr_forest_ff336699', content)
        self.assertEqual((self.res / "values/colors.xml").read_text(), '<resources><color name="brand">#112233</color></resources>')
        self.assertIn('@color/brand', (self.res / "layout/other.xml").read_text())
        self.assertTrue(any('drawable-xhdpi' in item['path'] for item in plan['files']))

    def test_second_plan_is_noop_and_rollback_restores_original_bytes(self):
        self.plan()
        apply_plan(self.run)
        second = self.plan(self.base / "second")
        self.assertEqual(second["files"], [])
        rollback(self.run)
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)
        self.assertEqual(list(self.res.rglob('lr_*')), [])

    def test_apply_rejects_changed_gradle_before_any_write(self):
        self.plan()
        (self.source / "build.gradle.kts").write_text('// User changed build\n')
        with self.assertRaisesRegex(ReskinError, 'Source conflict'):
            apply_plan(self.run)
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_apply_rejects_tampered_stage(self):
        plan = self.plan()
        (self.run / "staged" / plan["files"][0]["path"]).write_bytes(b'changed')
        with self.assertRaisesRegex(ReskinError, 'Staged file corrupted'):
            apply_plan(self.run)
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_rollback_conflict_prevents_partial_restoration(self):
        plan = self.plan()
        apply_plan(self.run)
        (self.source / self.layout).write_bytes(b'user modification')
        with self.assertRaisesRegex(ReskinError, 'Rollback conflict'):
            rollback(self.run)
        self.assertEqual((self.source / self.layout).read_bytes(), b'user modification')
        self.assertTrue(all((self.source / entry['path']).exists() for entry in plan['files']))

    def test_runtime_override_needs_review(self):
        code = self.source / 'app/src/main/java/Home.kt'
        code.parent.mkdir(parents=True)
        code.write_text('binding.title.setTextColor(123)')
        plan = self.plan()
        self.assertEqual(plan['state'], 'needs-input')
        self.assertTrue(any(issue['code'] == 'runtime-review-required' for issue in plan['issues']))
        with self.assertRaisesRegex(ReskinError, 'unresolved'):
            apply_plan(self.run)

    def test_missing_mapping_never_passes_empty_layout_grounding(self):
        self.skin['screens'][0]['mappings'] = []
        self.assertEqual(self.plan()['state'], 'needs-input')

    def test_geometry_edit_rejected_in_color_scope(self):
        self.skin['screens'][0]['mappings'][0]['properties'] = {'android:layout_marginTop': {'value': '4dp'}}
        plan = self.plan()
        self.assertEqual(plan['state'], 'needs-input')
        self.assertTrue(any('outside colors-icons' in issue.get('message', '') for issue in plan['issues']))

    def test_ambiguous_manifest_asset_is_not_silently_deduplicated(self):
        path = self.bundle_dir / 'assets.manifest.json'
        manifest = read_json(path)
        manifest['assets'].append(dict(manifest['assets'][0]))
        write_json(path, manifest)
        self.assertEqual(self.plan()['state'], 'needs-input')

    def test_source_consumers_are_transitive(self):
        (self.res / 'drawable/panel.xml').write_text('<shape xmlns:android="http://schemas.android.com/apk/res/android"><solid android:color="@color/brand"/></shape>')
        (self.res / 'layout/nested.xml').write_text('<View xmlns:android="http://schemas.android.com/apk/res/android" android:background="@drawable/panel"/>')
        consumers = affected_consumers(index_source(self.profile), 'color/brand')
        self.assertIn('app/src/main/res/layout/nested.xml', consumers)

    def test_namespace_alias_and_quoted_greater_than_are_preserved(self):
        raw = b'<TextView xmlns:a="http://schemas.android.com/apk/res/android" a:text="x > y" a:textColor = \'#fff\'/>'
        result = patch_attributes(raw, [{'index': 0, 'attribute': 'android:textColor', 'value': '@color/new'}, {'index': 0, 'attribute': 'android:tint', 'value': '@color/tint'}])
        self.assertIn(b'a:textColor = \'@color/new\'', result)
        self.assertIn(b'a:text="x > y"', result)
        self.assertIn(b'a:tint="@color/tint"', result)

    def test_figma_alpha_not_applied_twice(self):
        node = {'opacity': .5, 'fills': [{'type': 'SOLID', 'hex': '#33669980', 'opacity': .5}]}
        self.assertEqual(figma_color(node), '#40336699')

    def test_svg_rejects_clipping_and_transforms_instead_of_dropping_them(self):
        with self.assertRaisesRegex(ReskinError, 'not supported'):
            svg_to_vector(b'<svg viewBox="0 0 24 24"><g transform="translate(4 0)"><path d="M0 0h4v4z"/></g></svg>')
        with self.assertRaisesRegex(ReskinError, 'not supported'):
            svg_to_vector(b'<svg viewBox="0 0 24 24"><clipPath id="a"/></svg>')
        result = svg_to_vector(b'<svg viewBox="0 0 24 24"><g fill="#ff0000" opacity="0.5"><path d="M0 0h4v4z"/></g></svg>')
        self.assertIn(b'fillColor="#FFFF0000"', result)
        self.assertIn(b'fillAlpha="0.5"', result)

    def test_zip_bundle_prefix_and_traversal(self):
        archive = self.base / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for path in self.bundle_dir.rglob('*'):
                if path.is_file():
                    z.write(path, 'wrapped/' + path.relative_to(self.bundle_dir).as_posix())
        with Bundle(archive) as bundle:
            self.assertEqual(len(bundle.screens), 1)
            self.assertIsNotNone(bundle.reference('home.json'))
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('../escape', 'bad')
        with self.assertRaisesRegex(ReskinError, 'Unsafe'):
            Bundle(archive)

    def test_critical_region_catches_small_icon_error_hidden_in_global_score(self):
        reference = self.bundle_dir / 'screenshots/home.png'
        actual = self.base / 'actual.png'
        with Image.open(reference) as image:
            image.putpixel((0, 0), (255, 0, 0))
            image.save(actual)
        config = {'pixel_tolerance': 0, 'max_mismatch_ratio': .01, 'regions': [{'key': 'icon', 'bounds': [0, 0, 1, 1], 'max_mismatch_ratio': 0}]}
        result = compare_images(reference, actual, self.base / 'compare', config)
        self.assertTrue(result['regions'][0]['passed'])
        self.assertEqual(result['state'], 'visual-failed')

    def test_comparison_requires_threshold_and_never_resizes(self):
        reference = self.bundle_dir / 'screenshots/home.png'
        result = compare_images(reference, reference, self.base / 'compare', {})
        self.assertEqual(result['state'], 'needs-input')
        actual = self.base / 'actual.png'
        Image.new('RGB', (8, 8)).save(actual)
        result = compare_images(reference, actual, self.base / 'compare', {'max_mismatch_ratio': 1})
        self.assertEqual(result['issues'][0]['code'], 'viewport-mismatch')

    def test_masks_need_reason_and_cannot_hide_whole_image(self):
        reference = self.bundle_dir / 'screenshots/home.png'
        with self.assertRaisesRegex(ReskinError, 'reason'):
            compare_images(reference, reference, self.base / 'compare', {'max_mismatch_ratio': 0, 'masks': [{'bounds': [0, 0, 16, 16]}]})
        with self.assertRaisesRegex(ReskinError, 'Too much'):
            compare_images(reference, reference, self.base / 'compare', {'max_mismatch_ratio': 0, 'masks': [{'bounds': [0, 0, 16, 16], 'reason': 'dynamic'}]})

    def test_baseline_build_failure_does_not_apply(self):
        self.configure_commands()
        self.profile['build']['command'] = [sys.executable, '-c', 'raise SystemExit(1)']
        self.plan()
        self.assertEqual(execute(self.run)['state'], 'baseline-build-failed')
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_invalid_capture_is_reported_before_apply(self):
        self.configure_commands(b'not png')
        self.plan()
        self.assertEqual(execute(self.run)['state'], 'baseline-capture-failed')
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_visual_pass_without_smoke_is_not_completed(self):
        self.configure_commands()
        self.plan()
        result = execute(self.run)
        self.assertEqual(result['state'], 'visual-passed-unverified')
        self.assertTrue((self.run / 'iterations/home/1/overlay.png').exists())
        self.assertTrue((self.run / 'report.html').exists())

    def test_fixture_command_orchestration_requires_build_capture_and_smoke(self):
        self.configure_commands()
        self.profile['smoke'] = [[sys.executable, '-c', 'print("fixture smoke check")']]
        self.plan()
        result = execute(self.run)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['iteration'], 1)
        self.assertEqual(read_json(self.run / 'best-candidate.json')['score'], 0)

    def test_resume_refuses_user_changes_after_apply(self):
        self.configure_commands()
        self.plan()
        execute(self.run)
        (self.source / self.layout).write_bytes(b'user change')
        with self.assertRaisesRegex(ReskinError, 'Source changed'):
            execute(self.run)

    def test_cli_inspect_writes_report_without_mutation(self):
        profile_path = self.base / 'profile.json'
        write_json(profile_path, self.profile)
        before = index_source(self.profile)['files']
        process = subprocess.run([sys.executable, '-m', 'engine', 'inspect', '--project', str(profile_path), '--bundle', str(self.bundle_dir), '--output', str(self.base / 'inspect.json')], capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(index_source(self.profile)['files'], before)
        self.assertIn('source', read_json(self.base / 'inspect.json'))

    def test_repeated_apply_does_not_certify_changed_source(self):
        self.plan()
        apply_plan(self.run)
        (self.source / self.layout).write_bytes(b'user modification')
        with self.assertRaisesRegex(ReskinError, 'Source conflict'):
            apply_plan(self.run)

    def test_invalid_iteration_budget_does_not_apply(self):
        self.configure_commands()
        self.profile['max_iterations'] = 0
        self.plan()
        with self.assertRaisesRegex(ReskinError, 'max_iterations'):
            execute(self.run)
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_failed_candidate_stops_after_two_retries_without_improvement(self):
        buffer = io.BytesIO()
        Image.new('RGB', (16, 16), '#ff0000').save(buffer, format='PNG')
        self.configure_commands(buffer.getvalue())
        self.plan()
        self.assertEqual(execute(self.run)['state'], 'visual-failed')
        self.assertEqual(execute(self.run)['state'], 'visual-failed')
        stopped = execute(self.run)
        self.assertEqual(stopped['state'], 'iteration-stalled')
        self.assertEqual(execute(self.run)['iteration'], stopped['iteration'])

    def test_capture_command_cannot_change_source_and_still_complete(self):
        self.configure_commands()
        self.profile['smoke'] = [[sys.executable, '-c', 'from pathlib import Path; Path("app/src/main/res/layout/home.xml").write_text("changed")']]
        self.plan()
        self.assertEqual(execute(self.run)['state'], 'source-conflict')

    def test_run_artifacts_cannot_modify_input_bundle_or_android_source(self):
        with self.assertRaisesRegex(ReskinError, 'source sets'):
            self.plan(self.res / 'run')
        with self.assertRaisesRegex(ReskinError, 'input bundle'):
            self.plan(self.bundle_dir / 'run')
        self.assertFalse((self.res / 'run').exists())

    def test_color_patch_cannot_flatten_existing_background_shape(self):
        self.skin['screens'][0]['mappings'][1]['properties'] = {'android:background': 'fill'}
        (self.source / self.layout).write_bytes(self.original.replace(b'android:src="@drawable/old_icon"', b'android:background="@drawable/old_icon"'))
        result = self.plan()
        self.assertEqual(result['state'], 'needs-input')
        self.assertTrue(any('shape/stroke' in item.get('message', '') for item in result['issues']))


if __name__ == '__main__':
    unittest.main()
