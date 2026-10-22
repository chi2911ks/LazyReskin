from __future__ import annotations

import json
import os
import sys
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import test_engine
from engine.auto import automatic
from engine.common import ReskinError, read_json, write_json
from engine.device import resolve_apk
from engine.source import index_source
from engine.transaction import apply_plan


class AutoTests(unittest.TestCase):
    def setUp(self):
        test_engine.EngineTests.setUp(self)
        (self.source / 'app/src/main/AndroidManifest.xml').write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application><activity android:name=".HomeActivity"/></application></manifest>')
        (self.source / 'app/build.gradle.kts').write_text('plugins { id("com.android.application") }\nandroid { namespace = "com.example.app" }')
        for name in ('gradlew.bat', 'gradlew'):
            (self.source / name).write_text('fixture wrapper; not executed by AI orchestration tests')
        self.provider = self.base / 'provider.py'
        self.provider.write_text('''import argparse, json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--output');p.add_argument('--scenario',default='normal');a=p.parse_args()
r=json.loads(Path(a.request).read_text(encoding='utf-8'))
answer={'status':'ready','reason':'Existing title view and matching Figma text paint',
'module':'app','layout':'app/src/main/res/layout/home.xml','build_task':'assembleDebug',
'activity':'com.example.app.HomeActivity','wait_id':'title','wait_text':'',
'mappings':[{'node':'1:2','view':'title','attribute':'android:textColor','kind':'textColor','value':'', 'reason':'TEXT node mapped to existing title TextView'}]}
if a.scenario=='repair' and not r['feedback']:
    answer['mappings'][0]['node']='missing-node'
if a.scenario=='blocked':
    answer.update(status='blocked',reason='Design needs layout changes outside colors-icons')
if a.scenario=='command':
    answer['build_task']='assembleDebug && delete-files'
if a.scenario=='malformed':
    answer={'status':'ready'}
if a.scenario=='mutate':
    Path(r['source'],'app/src/main/res/layout/home.xml').write_text('provider modified source')
Path(a.output).write_text(json.dumps(answer),encoding='utf-8')
''', encoding='utf-8')
        self.job = self.base / 'automatic'

    tearDown = test_engine.EngineTests.tearDown

    def provider_command(self, scenario='normal'):
        return [sys.executable, str(self.provider), '--scenario', scenario]

    def test_auto_creates_configuration_and_mapping_from_only_source_and_bundle(self):
        before = index_source(self.profile)['files']
        state = automatic(self.source, self.bundle_dir, self.job, dry_run=True, agent_command=self.provider_command())
        self.assertEqual(state['state'], 'planned')
        self.assertEqual(index_source(self.profile)['files'], before)
        candidate = self.job / state['screens'][0]['candidate']
        plan = read_json(candidate / 'change-plan.json')
        self.assertEqual(plan['source'], str(self.job / 'workspace'))
        self.assertEqual(plan['operations'][0]['view'], 'title')
        self.assertTrue((self.job / 'report.html').exists())
        self.assertTrue((self.job / 'context/bundle/screenshots/home.png').exists())
        self.assertEqual((self.job / 'workspace' / self.layout).read_bytes(), self.original)

    def test_agent_automatically_repairs_mapping_from_validation_feedback(self):
        state = automatic(self.source, self.bundle_dir, self.job, dry_run=True, agent_command=self.provider_command('repair'), max_iterations=2)
        self.assertEqual(state['state'], 'planned')
        requests = sorted((self.job / 'proposals').rglob('request.json'))
        self.assertEqual(len(requests), 2)
        self.assertTrue(read_json(requests[1])['feedback']['planning_issues'])

    def test_blocked_design_does_not_apply_or_claim_completion(self):
        state = automatic(self.source, self.bundle_dir, self.job, dry_run=True, agent_command=self.provider_command('blocked'))
        self.assertEqual(state['state'], 'partial')
        self.assertIn('outside colors-icons', state['screens'][0]['reason'])
        self.assertFalse((self.job / 'candidates').exists())

    def test_ai_cannot_inject_an_arbitrary_build_command(self):
        state = automatic(self.source, self.bundle_dir, self.job, dry_run=True, agent_command=self.provider_command('command'), max_iterations=1)
        self.assertEqual(state['state'], 'partial')
        self.assertIn('not an arbitrary command', state['screens'][0]['reason'])
        self.assertFalse((self.job / 'candidates').exists())

    def test_malformed_agent_output_stops_without_repeated_calls(self):
        state = automatic(self.source, self.bundle_dir, self.job, dry_run=True, agent_command=self.provider_command('malformed'))
        self.assertEqual(state['state'], 'partial')
        self.assertEqual(len(list((self.job / 'proposals').rglob('request.json'))), 1)

    def test_source_mutation_by_custom_provider_stops_before_apply(self):
        state = automatic(self.source, self.bundle_dir, self.job, dry_run=True, agent_command=self.provider_command('mutate'))
        self.assertEqual(state['state'], 'partial')
        self.assertIn('provider modified', state['screens'][0]['reason'])
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)
        self.assertFalse((self.job / 'candidates').exists())

    def mock_execution(self, scores):
        iterator = iter(scores)
        def execute(candidate):
            checkpoint = apply_plan(candidate)
            score = next(iterator)
            state = 'completed' if score == 0 else 'visual-failed'
            checkpoint.update(state=state, validation={'screens':[{'comparison':{'regions':[{'mismatch_ratio':score}]}}]})
            write_json(candidate / 'checkpoint.json', checkpoint)
            return checkpoint
        return execute

    def test_auto_retries_failed_visual_candidate_and_keeps_successful_patch(self):
        with patch('engine.auto.device_ready'), patch('engine.auto.execute', side_effect=self.mock_execution([.4, 0, 0])) as execute:
            state = automatic(self.source, self.bundle_dir, self.job, agent_command=self.provider_command(), max_iterations=3)
        self.assertEqual(state['state'], 'completed')
        self.assertEqual(execute.call_count, 3)
        self.assertIn(b'@color/lr_auto_', (self.job / 'workspace' / self.layout).read_bytes())
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_final_source_regression_cannot_be_reported_as_completed(self):
        with patch('engine.auto.device_ready'), patch('engine.auto.execute', side_effect=self.mock_execution([0, .2])):
            state = automatic(self.source, self.bundle_dir, self.job, agent_command=self.provider_command())
        self.assertEqual(state['state'], 'partial')
        self.assertEqual(state['screens'][0]['state'], 'regression-failed')

    def test_conflicting_figma_states_cannot_overwrite_an_accepted_screen(self):
        other = json.loads(json.dumps(self.screen))
        other['flattened'][1]['fills'][0]['color'] = {'r':1, 'g':0, 'b':0}
        write_json(self.bundle_dir / 'screens/second.json', other)
        (self.bundle_dir / 'screenshots/second.png').write_bytes((self.bundle_dir / 'screenshots/home.png').read_bytes())
        with patch('engine.auto.device_ready'), patch('engine.auto.execute', side_effect=self.mock_execution([0, 0])) as execute:
            state = automatic(self.source, self.bundle_dir, self.job, agent_command=self.provider_command())
        self.assertEqual(state['state'], 'partial')
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(state['screens'][0]['state'], 'completed')
        self.assertEqual(state['screens'][1]['state'], 'needs-input')
        self.assertIn('selector/state mapping', state['screens'][1]['reason'])

    def test_single_auto_cli_command_needs_no_profile_or_skin_files(self):
        adapter = self.base / 'adapter.json'
        write_json(adapter, self.provider_command())
        process = subprocess.run([sys.executable, '-m', 'engine', 'auto', '--source', str(self.source),
                                  '--bundle', str(self.bundle_dir), '--output', str(self.job), '--dry-run',
                                  '--agent-command', str(adapter)], capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['state'], 'planned')
        self.assertTrue((self.job / 'report.html').is_file())

    def test_auto_restores_best_failed_candidate_on_budget_exhaustion(self):
        with patch('engine.auto.device_ready'), patch('engine.auto.execute', side_effect=self.mock_execution([.5, .2, .3])):
            state = automatic(self.source, self.bundle_dir, self.job, agent_command=self.provider_command(), max_iterations=3)
        self.assertEqual(state['state'], 'partial')
        self.assertEqual(state['screens'][0]['best_score'], .2)
        self.assertTrue(state['screens'][0]['candidate'].endswith('/2'))
        self.assertIn(b'@color/lr_auto_', (self.job / 'workspace' / self.layout).read_bytes())
        self.assertEqual((self.source / self.layout).read_bytes(), self.original)

    def test_auto_stops_two_non_improving_candidates(self):
        with patch('engine.auto.device_ready'), patch('engine.auto.execute', side_effect=self.mock_execution([.5, .5, .5])) as execute:
            state = automatic(self.source, self.bundle_dir, self.job, agent_command=self.provider_command())
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(state['screens'][0]['state'], 'iteration-stalled')

    def test_no_device_does_not_launch_provider_or_copy_source(self):
        with patch('engine.auto.device_ready', side_effect=ReskinError('No device')):
            with self.assertRaisesRegex(ReskinError, 'No device'):
                automatic(self.source, self.bundle_dir, self.job, agent_command=self.provider_command())
        self.assertFalse(self.job.exists())

    def test_auto_output_cannot_recurse_inside_original(self):
        with self.assertRaisesRegex(ReskinError, 'outside original'):
            automatic(self.source, self.bundle_dir, self.source / 'runs', dry_run=True, agent_command=self.provider_command())

    def test_apk_metadata_resolves_variant_and_rejects_escaping_output(self):
        root = self.base / 'outputs'
        folder = root / 'debug'
        folder.mkdir(parents=True)
        (folder / 'custom-name.apk').write_bytes(b'fixture apk')
        metadata = {'variantName':'for_dev_Debug','applicationId':'com.example.app.debug','elements':[{'filters':[],'outputFile':'custom-name.apk'}]}
        write_json(folder / 'output-metadata.json', metadata)
        apk, package = resolve_apk(root, 'ForDevDebug')
        self.assertEqual(apk.name, 'custom-name.apk')
        self.assertEqual(package, 'com.example.app.debug')
        metadata['elements'][0]['outputFile'] = '../../outside.apk'
        (self.base / 'outside.apk').write_bytes(b'fixture')
        write_json(folder / 'output-metadata.json', metadata)
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            resolve_apk(root, 'ForDevDebug')


if __name__ == '__main__':
    unittest.main()
