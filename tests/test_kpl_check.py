"""Deterministic fictional fixtures; no real model or semantic visual grading."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'kpl_check.py'
loader = importlib.util.spec_from_file_location('kpl_check', SCRIPT)
kpl = importlib.util.module_from_spec(loader)
loader.loader.exec_module(kpl)


class KPLCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kpl-state-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec = self.root / 'creative-spec.md'
        self.spec.write_text('# Fixture\n', encoding='utf-8')
        (self.root / 'brief.txt').write_text('One shot; duration two seconds.', encoding='utf-8')
        (self.root / 'script.txt').write_text('S01: two seconds; red scarf.', encoding='utf-8')
        (self.root / 'ref.png').write_bytes(b'fictional reference, not a real image')
        (self.root / 'out.png').write_bytes(b'fictional output, not a real image')
        (self.root / 'out.mp4').write_bytes(b'fictional video, not a playable video')
        self.state = {'version': 1, 'requirements': [
            {'id': 'R1', 'text': 'Two seconds', 'source': 'user message 1', 'constraints': {'duration': 2}},
            {'id': 'R2', 'text': 'Red scarf', 'source': 'user message 1'}],
            'artifacts': [], 'tasks': [], 'cuts': [], 'issues': []}
        for aid, name, kind in [('B', 'brief.txt', 'document'), ('D', 'script.txt', 'document'),
                                ('REF', 'ref.png', 'reference_image'), ('IMG', 'out.png', 'image'),
                                ('VID', 'out.mp4', 'video')]:
            self.state['artifacts'].append({'id': aid, 'path': name, 'kind': kind,
                                            'sha256': kpl.sha_file(self.root / name)})

    def report(self):
        return kpl.check(self.spec, self.state)

    def task(self, tid):
        return next(t for t in self.report()['tasks'] if t['id'] == tid)

    def add_task(self, **overrides):
        task = {'id': 'T1', 'phase': 'shot_spec', 'operation': 'write', 'action': 'Check fictional document', 'status': 'done',
                'depends_on': [], 'requirements': ['R1', 'R2'], 'inputs': ['B'], 'outputs': ['D'],
                'required_reviews': ['document'], 'shots': ['S01'], 'claims': [
                    {'requirement': 'R1', 'artifact': 'D', 'excerpt': 'two seconds', 'values': {'duration': 2}},
                    {'requirement': 'R2', 'artifact': 'D', 'excerpt': 'red scarf', 'values': {}}], 'reviews': []}
        task.update(overrides)
        self.state['tasks'].append(task)
        return task

    def evidence(self, aid, method='read_text', trace='test-trace', **extra):
        artifact = next(a for a in self.state['artifacts'] if a['id'] == aid)
        return {'artifact': aid, 'sha256': artifact['sha256'], 'method': method, 'trace': trace,
                'observation': 'Fixture observation with scope and actual detail', **extra}

    def review(self, task, kind, evidence, rid='V1'):
        basis = self.task(task['id'])['basis']
        task['reviews'].append({'id': rid, 'kind': kind, 'decision': 'passed',
                                'basis': basis, 'evidence': evidence})

    def test_document_omission_and_false_excerpt_are_detected(self):
        task = self.add_task()
        task['claims'] = task['claims'][:1]
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')])
        result = self.task('T1')
        self.assertEqual(result['status'], 'needs_fix')
        self.assertIn('requirement_uncovered', [p['code'] for p in result['problems']])
        task['claims'].append({'requirement': 'R2', 'artifact': 'D', 'excerpt': 'blue scarf'})
        self.assertIn('claim_missing_excerpt', [p['code'] for p in self.task('T1')['problems']])

    def test_document_explicit_constraint_conflict_is_detected(self):
        task = self.add_task()
        task['claims'][0]['values']['duration'] = 3
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')])
        result = self.task('T1')
        self.assertEqual(result['status'], 'needs_fix')
        self.assertIn('constraint_conflict', [p['code'] for p in result['problems']])

    def test_original_no_watermark_requirement_omitted_from_spec_is_not_accepted(self):
        (self.root / 'brief.txt').write_text('One still. Do not use a watermark.', encoding='utf-8')
        (self.root / 'script.txt').write_text('A single still with a visible watermark.', encoding='utf-8')
        brief = next(a for a in self.state['artifacts'] if a['id'] == 'B')
        doc = next(a for a in self.state['artifacts'] if a['id'] == 'D')
        brief.update(role='user_source', sha256=kpl.sha_file(self.root / 'brief.txt'))
        doc['sha256'] = kpl.sha_file(self.root / 'script.txt')
        self.state['requirements'] = [{'id': 'R1', 'text': 'One still', 'source': 'original brief'}]
        task = self.add_task(requirements=['R1'], claims=[
            {'requirement': 'R1', 'artifact': 'D', 'excerpt': 'single still', 'values': {}}])
        # The spec and output agree, but the reviewer separately extracts both original requirements.
        self.state['source_audits'] = [{'id': 'SA1', 'source_artifact': 'B', 'tasks': ['T1'],
            'trace': 'fixture read of original brief', 'items': [
                {'id': 'U1', 'excerpt': 'One still.', 'requirements': ['R1']},
                {'id': 'U2', 'excerpt': 'Do not use a watermark.', 'requirements': []}]}]
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')])
        result = self.task('T1')
        self.assertEqual(result['status'], 'needs_fix')
        self.assertIn('source_item_unmapped', [p['code'] for p in result['problems']])
        self.state['requirements'].append({'id': 'R2', 'text': 'No watermark', 'source': 'original brief'})
        self.state['source_audits'][0]['items'][1]['requirements'] = ['R2']
        task['requirements'].append('R2')
        self.assertIn('requirement_uncovered', [p['code'] for p in self.task('T1')['problems']])

    def test_original_source_marked_as_user_source_requires_separate_audit(self):
        next(a for a in self.state['artifacts'] if a['id'] == 'B')['role'] = 'user_source'
        self.add_task()
        self.assertIn('source_audit_missing', [p['code'] for p in self.task('T1')['problems']])

    def test_image_claim_without_tool_trace_cannot_pass(self):
        task = self.add_task(phase='asset_prep', action='Inspect output and reference', requirements=[],
                             inputs=['REF'], outputs=['IMG'], required_reviews=['image'], claims=[])
        self.review(task, 'image', [self.evidence('REF', 'view_image', trace=''),
                                    self.evidence('IMG', 'view_image', trace='')])
        task['reviews'][0]['self_report'] = 'I saw it and it is perfect'
        self.assertEqual(self.task('T1')['status'], 'needs_review')
        task['reviews'][0]['evidence'] = [self.evidence('REF', 'view_image'), self.evidence('IMG', 'view_image')]
        result = self.task('T1')
        self.assertEqual(result['status'], 'recorded_pass')
        self.assertFalse(result['checks'][0]['trace_verified_by_script'])
        self.assertFalse(self.report()['script_verifies_viewing_or_semantics'])

    def test_image_reference_is_not_optional_when_bound_as_input(self):
        task = self.add_task(phase='asset_prep', requirements=[], inputs=['REF'], outputs=['IMG'],
                             required_reviews=['image'], claims=[])
        self.review(task, 'image', [self.evidence('IMG', 'view_image')])
        self.assertEqual(self.task('T1')['status'], 'needs_review')

    def test_video_static_frame_and_fps_cannot_replace_original_speed_audio_review(self):
        task = self.add_task(phase='sample_review', requirements=[], inputs=[], outputs=['VID'],
                             audio_required=True, required_reviews=['video'], claims=[])
        self.review(task, 'video', [self.evidence('VID', 'view_image')])
        self.assertEqual(self.task('T1')['status'], 'needs_review')
        task['reviews'][0]['evidence'] = [self.evidence('VID', 'view_video_1x', audio_observed=False)]
        self.assertEqual(self.task('T1')['status'], 'needs_review')
        task['reviews'][0]['evidence'] = [self.evidence('VID', 'view_video_1x', audio_observed=True)]
        self.assertEqual(self.task('T1')['status'], 'recorded_pass')
        self.assertFalse(self.task('T1')['checks'][0]['trace_verified_by_script'])

    def test_changed_material_invalidates_evidence_and_only_dependent_tasks(self):
        first = self.add_task(phase='asset_prep', requirements=[], inputs=['REF'], outputs=['IMG'],
                              required_reviews=['image'], claims=[])
        self.review(first, 'image', [self.evidence('REF', 'view_image'), self.evidence('IMG', 'view_image')])
        self.add_task(id='T2', phase='sample_review', status='pending', depends_on=['T1'],
                      requirements=[], inputs=['IMG'], outputs=[], required_reviews=[], claims=[])
        self.add_task(id='T3', phase='rhythm', status='pending', depends_on=[], requirements=[],
                      inputs=['B'], outputs=[], required_reviews=[], claims=[])
        (self.root / 'ref.png').write_bytes(b'changed reference')
        self.assertEqual(self.task('T1')['status'], 'stale')
        self.assertEqual(self.task('T2')['status'], 'blocked_dependency')
        self.assertEqual(self.task('T3')['status'], 'ready')
        ref = next(a for a in self.state['artifacts'] if a['id'] == 'REF')
        ref['sha256'] = kpl.sha_file(self.root / 'ref.png')
        self.assertEqual(self.task('T1')['status'], 'needs_review')

    def test_upstream_requirement_change_invalidates_dependent_review(self):
        task = self.add_task()
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')])
        self.assertEqual(self.task('T1')['status'], 'recorded_pass')
        self.state['requirements'][1]['text'] = 'Blue scarf instead'
        self.assertEqual(self.task('T1')['status'], 'needs_review')

    def test_montage_is_valid_but_continuity_conflict_is_local(self):
        self.add_task(id='T1', status='pending', requirements=[], inputs=['B'], outputs=[], claims=[],
                      required_reviews=[], shots=['S02'])
        self.add_task(id='T2', status='pending', requirements=[], inputs=['B'], outputs=[], claims=[],
                      required_reviews=[], shots=['S09'])
        self.state['requirements'].append({'id': 'R3', 'text': 'Use intentional image montage', 'source': 'upstream design'})
        cut = {'id': 'C1', 'from': 'S01', 'to': 'S02', 'kind': 'montage', 'purpose': 'image contrast',
               'intent_ref': {'requirement': 'R3', 'trace': 'fixture upstream design read'},
               'exit': {'direction': 'left'}, 'entry': {'direction': 'right'}}
        self.state['cuts'] = [cut]
        self.assertEqual(self.task('T1')['status'], 'ready')
        cut['kind'] = 'continuous'
        self.assertEqual(self.task('T1')['status'], 'needs_fix')
        self.assertEqual(self.task('T2')['status'], 'ready')
        cut['exceptions'] = {'direction': 'intentional change of direction'}
        self.assertEqual(self.task('T1')['status'], 'ready')

    def test_montage_without_upstream_intent_cannot_exempt_continuity(self):
        self.add_task(status='pending', requirements=[], inputs=['B'], outputs=[], claims=[],
                      required_reviews=[], shots=['S02'])
        cut = {'id': 'C1', 'from': 'S01', 'to': 'S02', 'kind': 'montage', 'purpose': 'post-hoc label'}
        self.state['cuts'] = [cut]
        result = self.task('T1')
        self.assertEqual(result['status'], 'needs_fix')
        self.assertIn('cut_intent_missing', [p['code'] for p in result['problems']])
        cut['intent_ref'] = {'artifact': 'B', 'excerpt': 'absent intent', 'trace': 'fixture read'}
        self.assertEqual(self.task('T1')['status'], 'needs_fix')

    def test_changed_shot_invalidates_adjacent_cut_and_final_but_not_other_shot(self):
        first = self.add_task(id='T1', shots=['S01'])
        self.review(first, 'document', [self.evidence('B'), self.evidence('D')])
        second = self.add_task(id='T2', operation='review', phase='shot_spec', status='done',
                               requirements=[], inputs=['B'], outputs=[], required_reviews=['design'],
                               claims=[], shots=['S02'])
        self.review(second, 'design', [self.evidence('B')])
        cut_task = self.add_task(id='T3', operation='review', phase='sample_review', status='done',
                                 requirements=[], inputs=['B'], outputs=[], required_reviews=['design'],
                                 claims=[], shots=['S01', 'S02'], depends_on=['T1', 'T2'])
        self.review(cut_task, 'design', [self.evidence('B')])
        self.add_task(id='T4', operation='review', phase='edit_review', status='pending',
                      requirements=[], inputs=['VID'], outputs=['VID'], required_reviews=['video'],
                      claims=[], shots=['S01', 'S02'], depends_on=['T3'], delivery={'artifact': 'VID'})
        self.assertEqual(self.task('T2')['status'], 'recorded_pass')
        self.assertEqual(self.task('T3')['status'], 'recorded_pass')
        (self.root / 'script.txt').write_text('Changed S01', encoding='utf-8')
        self.assertNotEqual(self.task('T1')['status'], 'recorded_pass')
        self.assertEqual(self.task('T2')['status'], 'recorded_pass')
        self.assertEqual(self.task('T3')['status'], 'blocked_dependency')
        self.assertEqual(self.task('T4')['status'], 'blocked_dependency')

    def test_interrupted_job_with_existing_output_is_inspected_not_resubmitted(self):
        task = self.add_task(phase='asset_prep', status='running', requirements=[], inputs=['REF'], outputs=['IMG'],
                      required_reviews=['image'], claims=[])
        result = self.task('T1')
        self.assertEqual(result['status'], 'running_or_unknown')
        self.assertEqual(result['action'], 'check_existing_job')
        self.assertEqual(self.report()['next_candidates'][0]['action'], 'check_existing_job')
        task['status'] = 'pending'
        result = self.task('T1')
        self.assertEqual(result['status'], 'output_exists')
        self.assertEqual(result['action'], 'inspect_existing_output')

    def test_generation_attempts_query_uncertain_jobs_and_respect_budget(self):
        self.state['artifacts'].append({'id': 'NEW', 'path': 'not-created.png', 'kind': 'image', 'sha256': None})
        task = self.add_task(phase='asset_prep', operation='generate', status='pending', requirements=[],
                             inputs=['REF'], outputs=['NEW'], required_reviews=['image'], claims=[], attempts=[])
        self.assertEqual(self.task('T1')['status'], 'ready')
        self.assertIn('generate', self.task('T1')['capabilities_to_verify'])
        self.assertFalse(self.task('T1')['capabilities_verified_by_script'])
        task['attempts'] = [{'job_ref': 'job-1', 'status': 'unknown'}]
        self.assertEqual(self.task('T1')['action'], 'check_existing_job')
        self.assertEqual(self.task('T1')['job_ref'], 'job-1')
        task['attempts'][0]['status'] = 'failed'
        self.assertEqual(self.task('T1')['status'], 'needs_fix')
        task['attempts'][0]['repair'] = 'correct the reference role'
        self.assertEqual(self.task('T1')['status'], 'ready')
        task['attempts'].extend([{'job_ref': 'job-2', 'status': 'failed', 'repair': 'correct pose'},
                                 {'job_ref': 'job-3', 'status': 'failed', 'repair': 'correct crop'}])
        self.assertEqual(self.task('T1')['status'], 'retry_budget_exhausted')

    def test_interruption_before_receipt_never_resubmits_blindly(self):
        self.state['artifacts'].append({'id': 'NEW', 'path': 'not-created.png', 'kind': 'image', 'sha256': None})
        self.add_task(phase='asset_prep', operation='generate', status='pending', requirements=[],
                      inputs=['REF'], outputs=['NEW'], required_reviews=['image'], claims=[],
                      attempts=[{'job_ref': None, 'status': 'unknown', 'uncertainty': 'request sent; receipt unavailable'}])
        result = self.task('T1')
        self.assertEqual(result['action'], 'check_existing_job')
        self.assertIsNone(result['job_ref'])

    def test_prior_success_does_not_hide_latest_failed_attempt(self):
        self.state['artifacts'].append({'id': 'NEW', 'path': 'not-created.png', 'kind': 'image', 'sha256': None})
        task = self.add_task(phase='asset_prep', operation='generate', status='pending', requirements=[],
                             inputs=['REF'], outputs=['NEW'], required_reviews=['image'], claims=[],
                             attempts=[{'job_ref': 'job-1', 'status': 'succeeded'},
                                       {'job_ref': 'job-2', 'status': 'failed', 'repair': 'correct reference'}])
        self.assertEqual(self.task('T1')['status'], 'ready')
        task['attempts'][1]['status'] = 'unknown'
        self.assertEqual(self.task('T1')['action'], 'check_existing_job')

    def test_static_portrait_does_not_need_a_motion_template(self):
        self.add_task(status='pending', action='Intentional static emotional portrait', requirements=[],
                      inputs=['B'], outputs=[], required_reviews=[], claims=[], shots=['S01'])
        self.assertEqual(self.task('T1')['status'], 'ready')

    def test_machine_state_cannot_omit_operation_classification(self):
        task = self.add_task(status='pending', requirements=[], inputs=['B'], outputs=[],
                             required_reviews=[], claims=[])
        task.pop('operation')
        self.assertIn('missing_operation', [p['code'] for p in self.task('T1')['problems']])

    def test_done_task_must_have_review_and_blocks_dependent_until_recorded(self):
        first = self.add_task()
        self.add_task(id='T2', phase='rhythm', status='pending', depends_on=['T1'],
                      requirements=[], inputs=['B'], outputs=[], required_reviews=[], claims=[])
        self.assertEqual(self.task('T1')['status'], 'needs_review')
        self.assertEqual(self.task('T2')['status'], 'blocked_dependency')
        self.review(first, 'document', [self.evidence('B'), self.evidence('D')])
        report = self.report()
        self.assertEqual(next(t for t in report['tasks'] if t['id'] == 'T2')['status'], 'ready')
        self.assertTrue(report['trace_checks_required'])

    def test_major_issue_requires_fresh_recheck_or_explicit_user_decision(self):
        task = self.add_task()
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')], rid='V1')
        issue = {'id': 'I1', 'task': 'T1', 'severity': 'major', 'status': 'open', 'opened_review_id': 'V1'}
        self.state['issues'] = [issue]
        self.assertEqual(self.task('T1')['status'], 'needs_fix')
        issue.update(status='resolved', recheck_review_id='V1')
        self.assertEqual(self.task('T1')['status'], 'needs_fix')
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')], rid='V2')
        issue['recheck_review_id'] = 'V2'
        self.assertEqual(self.task('T1')['status'], 'recorded_pass')

    def test_newer_failed_review_overrides_older_recorded_pass(self):
        task = self.add_task()
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')], rid='V1')
        self.assertEqual(self.task('T1')['status'], 'recorded_pass')
        task['reviews'].append({'id': 'V2', 'kind': 'document', 'decision': 'needs_fix',
                                'basis': self.task('T1')['basis'], 'evidence': []})
        self.assertEqual(self.task('T1')['status'], 'needs_fix')

    def test_newer_unverified_review_does_not_reuse_older_pass(self):
        task = self.add_task()
        self.review(task, 'document', [self.evidence('B'), self.evidence('D')], rid='V1')
        task['reviews'].append({'id': 'V2', 'kind': 'document', 'decision': 'unverified',
                                'basis': self.task('T1')['basis'], 'evidence': []})
        self.assertEqual(self.task('T1')['status'], 'needs_review')

    def test_unmapped_requirements_block_final_review_but_not_independent_work(self):
        self.add_task(id='T1', phase='rhythm', status='pending', requirements=[], inputs=['B'],
                      outputs=[], required_reviews=[], claims=[])
        self.add_task(id='T2', phase='edit_review', status='pending', requirements=[], inputs=['B'],
                      outputs=[], required_reviews=[], claims=[])
        self.assertEqual(self.task('T1')['status'], 'ready')
        final = self.task('T2')
        self.assertEqual(final['status'], 'needs_fix')
        self.assertIn('requirement_unmapped', [e['code'] for e in final['problems']])

    def test_not_applicable_cannot_hide_an_existing_video_output(self):
        self.add_task(phase='sample_review', requirements=[], inputs=[], outputs=['VID'],
                      required_reviews=['technical'], claims=[], not_applicable={'video': 'text only'})
        self.assertIn('bad_na', [p['code'] for p in self.task('T1')['problems']])

    def test_final_delivery_binds_sequence_text_sources_and_output_version(self):
        self.state['requirements'] = [{'id': 'R2', 'text': 'Title ALPHA', 'source': 'original brief'}]
        for aid, name in [('V1', 's01.mp4'), ('V2', 's02.mp4')]:
            path = self.root / name
            path.write_bytes(('fictional ' + aid).encode())
            self.state['artifacts'].append({'id': aid, 'path': name, 'kind': 'video', 'sha256': kpl.sha_file(path)})
        v1 = next(a['sha256'] for a in self.state['artifacts'] if a['id'] == 'V1')
        v2 = next(a['sha256'] for a in self.state['artifacts'] if a['id'] == 'V2')
        delivery = {'artifact': 'VID', 'observed_shots': ['S01', 'S02'],
                    'source_versions': [{'shot': 'S01', 'artifact': 'V1', 'sha256': v1},
                                        {'shot': 'S02', 'artifact': 'V2', 'sha256': v2}],
                    'text_required': True, 'text_status': 'passed', 'text_observation': 'fictional full title visible',
                    'expected_text': [{'requirement': 'R2', 'text': 'ALPHA'}], 'observed_text': ['ALPHA']}
        task = self.add_task(phase='edit_review', operation='review', requirements=['R2'],
                             inputs=['V1', 'V2'], outputs=['VID'], required_reviews=['video'], claims=[],
                             shots=['S01', 'S02'], audio_required=True, delivery=delivery)
        self.review(task, 'video', [self.evidence('V1', 'view_video_1x'), self.evidence('V2', 'view_video_1x'),
                                   self.evidence('VID', 'view_video_1x', audio_observed=True)])
        self.assertEqual(self.task('T1')['status'], 'recorded_pass')
        self.assertFalse(self.task('T1')['checks'][0]['trace_verified_by_script'])
        delivery['observed_shots'] = ['S01']
        self.assertIn('delivery_sequence', [p['code'] for p in self.task('T1')['problems']])
        delivery['observed_shots'] = ['S01', 'S02']
        delivery['text_status'] = 'needs_fix'
        delivery['text_observation'] = 'fictional subtitle cropped'
        self.assertIn('delivery_text', [p['code'] for p in self.task('T1')['problems']])
        delivery['text_status'] = 'passed'
        delivery['text_observation'] = 'fictional full title visible'
        delivery['source_versions'][1]['sha256'] = '0' * 64
        self.assertIn('delivery_versions', [p['code'] for p in self.task('T1')['problems']])
        delivery['source_versions'][1]['sha256'] = v2
        (self.root / 'out.mp4').write_bytes(b'replaced final output')
        self.assertNotEqual(self.task('T1')['status'], 'recorded_pass')

    def test_legacy_spec_is_read_only_and_duplicate_state_blocks_fail(self):
        old = self.spec.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.spec)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)['legacy'])
        self.assertEqual(self.spec.read_bytes(), old)
        block = '```kpl-state\n{"version":1}\n```\n'
        self.spec.write_text(block + block, encoding='utf-8')
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.spec)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)

    def test_cycle_and_escaping_paths_do_not_produce_executable_candidate(self):
        self.add_task(id='T1', status='pending', depends_on=['T2'])
        self.add_task(id='T2', status='pending', depends_on=['T1'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.report()
        self.state['tasks'][0]['depends_on'] = []
        self.state['artifacts'][0]['path'] = '../outside.txt'
        result = self.task('T1')
        self.assertEqual(result['status'], 'needs_fix')
        self.assertIn('artifact_outside_project', [p['code'] for p in result['problems']])

    def test_cli_hash_and_explicit_external_reference(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--hash', str(self.root / 'ref.png')],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['sha256'], kpl.sha_file(self.root / 'ref.png'))
        with tempfile.TemporaryDirectory(prefix='kpl-allowed-reference-') as outer:
            path = Path(outer) / 'reference.png'
            path.write_bytes(b'external fictional reference')
            ref = next(a for a in self.state['artifacts'] if a['id'] == 'REF')
            ref.update(path=str(path), sha256=kpl.sha_file(path))
            self.add_task(status='pending', requirements=[], inputs=['REF'], outputs=[], required_reviews=[], claims=[])
            self.assertEqual(self.task('T1')['status'], 'needs_fix')
            allowed = kpl.check(self.spec, self.state, [Path(outer)])
            self.assertEqual(allowed['artifacts']['REF']['status'], 'current')
            self.assertEqual(allowed['tasks'][0]['status'], 'ready')

    def test_real_cli_resume_example_with_one_spec_file(self):
        document = next(a for a in self.state['artifacts'] if a['id'] == 'D')
        document.update(path='created.txt', sha256=None)
        task = self.add_task(status='pending', requirements=['R1'], claims=[], reviews=[])
        self.add_task(id='T2', phase='shot_group', operation='write', status='pending', depends_on=['T1'],
                      requirements=[], inputs=['B'], outputs=[], required_reviews=[], claims=[])
        def run():
            self.spec.write_text('# Creative spec\n\n```kpl-state\n' + json.dumps(self.state) + '\n```\n', encoding='utf-8')
            proc = subprocess.run([sys.executable, str(SCRIPT), str(self.spec)], capture_output=True,
                                  text=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return json.loads(proc.stdout)
        first = run()
        self.assertEqual(first['tasks'][0]['action'], 'execute_with_authorization')
        self.assertEqual(first['tasks'][1]['status'], 'blocked_dependency')
        (self.root / 'created.txt').write_text('S01: two seconds.', encoding='utf-8')
        document['sha256'] = kpl.sha_file(self.root / 'created.txt')
        task['claims'] = [{'requirement': 'R1', 'artifact': 'D', 'excerpt': 'two seconds', 'values': {'duration': 2}}]
        current = run()
        self.assertEqual(current['tasks'][0]['action'], 'inspect_existing_output')
        task['status'] = 'done'
        task['reviews'] = [{'id': 'V1', 'kind': 'document', 'decision': 'passed',
                            'basis': current['tasks'][0]['basis'],
                            'evidence': [self.evidence('B'), self.evidence('D')]}]
        resumed = run()
        self.assertEqual(resumed['tasks'][0]['status'], 'recorded_pass')
        self.assertEqual(resumed['tasks'][1]['status'], 'ready')
        self.assertFalse(resumed['tasks'][0]['checks'][0]['trace_verified_by_script'])
        (self.root / 'created.txt').write_text('S01: changed.', encoding='utf-8')
        stale = run()
        self.assertNotEqual(stale['tasks'][0]['status'], 'recorded_pass')
        self.assertEqual(stale['tasks'][1]['status'], 'blocked_dependency')


if __name__ == '__main__':
    unittest.main(verbosity=2)
