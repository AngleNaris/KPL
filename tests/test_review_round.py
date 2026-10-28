"""Fictional state/trace fixtures; checks structure, never semantic or delivery truth."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'kpl_check.py'
loader = importlib.util.spec_from_file_location('kpl_check_review_tests', SCRIPT)
kpl = importlib.util.module_from_spec(loader)
loader.loader.exec_module(kpl)


class ReviewRoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kpl-review-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec = self.root / 'review.md'
        self.spec.write_text('fictional review record', encoding='utf-8')
        self.state = {'version': 1, 'requirements': [{'id': 'R1', 'text': 'a fictional shot', 'source': 'brief'}],
                      'artifacts': [], 'tasks': [], 'cuts': [], 'issues': []}
        self.add_artifact('BRIEF', 'brief.txt', 'document', 'user_source', 'Original fictional user brief')
        self.add_artifact('SHOT', 'shot.txt', 'document', 'shot_design', 'Two shot design')
        self.add_artifact('PROMPT', 'prompt.txt', 'document', 'video_prompt', 'Fictional prompt')
        self.add_artifact('IMAGE', 'image.png', 'image', None, b'not a real image; no actual viewing claim')
        self.add_artifact('DELIVERABLE', 'delivery.txt', 'document', 'deliverable', 'Fictional project document')
        self.state['review_round'] = {
            'id': 'REV1', 'mode': 'review', 'scope': 'fictional project',
            'settings': {key: {'value': value, 'source': 'fictional confirmed brief', 'authority': 'confirmed'}
                         for key, value in [('resolution', '1920x1080'), ('fps', '24'), ('aspect_ratio', '16:9')]},
            'questions': [], 'stages': [{'id': sid, 'status': 'pending', 'inputs': [], 'requirements': [],
                                         'checks': [], 'evidence': []} for sid in kpl.REVIEW_STAGES]}

    def add_artifact(self, aid, name, kind, role, content):
        path = self.root / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        row = {'id': aid, 'path': name, 'kind': kind, 'sha256': kpl.sha_file(path)}
        if role:
            row['role'] = role
        self.state['artifacts'].append(row)

    def evidence(self, aid, method='read_text'):
        row = next(a for a in self.state['artifacts'] if a['id'] == aid)
        return {'artifact': aid, 'sha256': row['sha256'], 'method': method,
                'trace': 'fictional trace for deterministic test', 'observation': 'fictional inspection'}

    def result(self):
        return kpl.check(self.spec, self.state, require_project_review=True)['review_round']

    def stage_result(self, sid):
        return next(s for s in self.result()['stages'] if s['id'] == sid)

    def complete(self, sid, inputs, outcome='passed', evidence=None, reason=None, blocks=None):
        stage = next(s for s in self.state['review_round']['stages'] if s['id'] == sid)
        summary = {k: f'{sid} {k} concrete fictional text' for k in kpl.SUMMARY_FIELDS}
        report_id = 'REPORT_' + sid.upper()
        self.add_artifact(report_id, 'report_' + sid + '.md', 'document', None, '\n'.join(summary.values()))
        report_artifact = next(a for a in self.state['artifacts'] if a['id'] == report_id)
        stage.update(status=outcome, inputs=inputs, checks=[{'name': 'fixture', 'result': 'issue' if outcome == 'failed' else 'checked',
                      'observation': 'fictional observation'}], summary=summary,
                     report={'artifact': report_id, 'sha256': report_artifact['sha256'], 'delivery': 'generated_only'},
                     evidence=evidence if evidence is not None else [self.evidence(a, 'view_image' if a == 'IMAGE' else 'read_text') for a in inputs],
                     blocks=blocks or [])
        if reason:
            stage['reason'] = reason
        if sid == 'deliverables' and outcome == 'passed':
            stage['evidence'].append(self.evidence(report_id))
        stage['basis'] = self.stage_result(sid)['basis']
        return stage

    def complete_all(self):
        for sid, inputs in [('global', ['BRIEF']), ('directing', ['SHOT']), ('prompts', ['PROMPT']),
                            ('assets', ['IMAGE']), ('deliverables', ['DELIVERABLE'])]:
            self.complete(sid, inputs)

    def test_missing_review_round_is_explicit_and_legacy_is_unchanged(self):
        del self.state['review_round']
        result = kpl.check(self.spec, self.state, require_project_review=True)
        self.assertEqual(result['review_round']['status'], 'missing')
        self.assertEqual(result['review_round']['next_stage'], 'global')
        self.assertNotIn('review_round', kpl.check(self.spec, self.state))

    def test_all_five_stages_have_fixed_order(self):
        self.state['review_round']['stages'][1:3] = reversed(self.state['review_round']['stages'][1:3])
        with self.assertRaisesRegex(ValueError, 'required order'):
            self.result()

    def test_reports_enable_progress_but_cannot_prove_delivery(self):
        self.complete_all()
        result = self.result()
        self.assertEqual(result['status'], 'recorded_pass')
        self.assertFalse(result['semantics_verified_by_script'])
        self.assertTrue(all(s['report_delivery'] == 'unconfirmed' for s in result['stages']))
        self.assertTrue(all(not s['delivery_verified_by_script'] for s in result['stages']))
        stage = self.state['review_round']['stages'][0]
        stage['report'].update(delivery='claimed_user_visible', delivery_trace='fictional delivery event')
        self.assertEqual(self.stage_result('global')['report_delivery'], 'unconfirmed')

    def test_missing_report_blocks_later_stages_and_resume_finds_report(self):
        self.complete('global', ['BRIEF'])
        self.complete('directing', ['SHOT'])
        report = self.root / 'report_global.md'
        original = report.read_bytes()
        report.unlink()
        self.assertEqual(self.stage_result('global')['status'], 'needs_report')
        self.assertEqual(self.stage_result('directing')['status'], 'blocked_order')
        self.assertEqual(self.result()['next_stage'], 'global')
        report.write_bytes(original)
        self.assertEqual(self.stage_result('global')['status'], 'recorded_pass')
        self.assertEqual(self.stage_result('directing')['status'], 'recorded_pass')

    def test_author_pass_and_file_exists_do_not_replace_image_observation(self):
        self.complete('global', ['BRIEF'])
        self.complete('directing', ['SHOT'])
        self.complete('prompts', ['PROMPT'])
        self.complete('assets', ['IMAGE'], evidence=[])
        result = self.stage_result('assets')
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertIn('input_not_reviewed:IMAGE', result['problems'])
        self.assertFalse(result['semantics_verified_by_script'])

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'overview tool requires Pillow')
    def test_actual_overview_index_records_partial_coverage_without_claiming_full_image_pass(self):
        from PIL import Image
        image = self.root / 'image.png'
        Image.new('RGB', (100, 80), 'blue').save(image)
        asset = next(a for a in self.state['artifacts'] if a['id'] == 'IMAGE')
        asset['sha256'] = kpl.sha_file(image)
        manifest = self.root / 'board-manifest.json'
        manifest.write_text(json.dumps({'version': 1, 'shots': [{'id': 'S01', 'media': 'image.png', 'duration': 1}]}), encoding='utf-8')
        board_dir = self.root / 'board'
        script = SCRIPT.parent / 'review_boards.py'
        subprocess.run([sys.executable, '-B', '-X', 'utf8', str(script), 'sequence', '--manifest', str(manifest),
                        '--out', str(board_dir)], capture_output=True, check=True)
        page, index = board_dir / 'sequence-001.png', board_dir / 'index.json'
        for aid, file, kind in [('BOARD', page, 'image'), ('BOARD_INDEX', index, 'document')]:
            self.state['artifacts'].append({'id': aid, 'kind': kind, 'path': str(file.relative_to(self.root)), 'sha256': kpl.sha_file(file)})
        self.complete('global', ['BRIEF'])
        self.complete('directing', ['SHOT'])
        self.complete('prompts', ['PROMPT'])
        ev = {'artifact': 'BOARD', 'sha256': kpl.sha_file(page), 'method': 'view_board', 'board_index': 'BOARD_INDEX',
              'board_index_sha256': kpl.sha_file(index), 'trace': 'fictional host trace',
              'observation': 'thumbnail only; fine detail unverified'}
        stage = self.complete('assets', ['IMAGE'], outcome='unverified', evidence=[ev], reason='Fine detail not examined')
        result = self.stage_result('assets')
        self.assertEqual(result['status'], 'recorded_unverified')
        self.assertEqual(result['overview_source_coverage'], ['IMAGE'])
        self.assertFalse(result['semantics_verified_by_script'])
        stage['status'] = 'passed'
        stage['basis'] = self.stage_result('assets')['basis']
        result = self.stage_result('assets')
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertIn('overview_only_unverified:IMAGE', result['problems'])
        stage['status'] = 'unverified'
        stage['basis'] = self.stage_result('assets')['basis']
        index.write_text('{}', encoding='utf-8')
        self.assertIn('board_mapping_invalid_or_stale', self.stage_result('assets')['problems'])

    def test_prompt_and_image_inputs_cannot_be_omitted_for_pass(self):
        self.complete('global', ['BRIEF'])
        self.complete('directing', ['SHOT'])
        self.complete('prompts', [], evidence=[])
        result = self.stage_result('prompts')
        self.assertEqual(result['status'], 'blocked_input')
        self.assertIn('required_input_missing:video_prompt', result['problems'])
        self.state['review_round']['stages'][2]['status'] = 'blocked'
        self.state['review_round']['stages'][2]['reason'] = 'No prompt file exists.'
        self.state['review_round']['stages'][2]['basis'] = self.stage_result('prompts')['basis']
        self.complete('assets', [], evidence=[])
        self.assertIn('required_input_missing:image', self.stage_result('assets')['problems'])
        self.assertEqual(self.stage_result('assets')['status'], 'blocked_input')

    def test_missing_existing_input_is_blocked_not_a_pass(self):
        self.complete('global', ['BRIEF'])
        self.complete('directing', ['SHOT'])
        self.complete('prompts', ['PROMPT'])
        (self.root / 'prompt.txt').unlink()
        result = self.stage_result('prompts')
        self.assertEqual(result['status'], 'blocked_input')
        self.assertEqual(result['action'], 'locate_or_ask_about_missing_input')

    def test_global_settings_require_questions_and_answers_invalidate_evidence(self):
        review = self.state['review_round']
        review['settings'] = {}
        global_stage = self.complete('global', ['BRIEF'], outcome='unverified', reason='Settings unknown')
        result = self.stage_result('global')
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertIn('missing_question:fps', result['problems'])
        for i, field in enumerate(kpl.REQUIRED_SETTINGS):
            review['questions'].append({'id': 'Q' + str(i), 'topic': field, 'question': 'What is ' + field + '?',
                                        'stages': ['global', 'directing'], 'status': 'open'})
        global_stage['basis'] = self.stage_result('global')['basis']
        self.assertEqual(self.stage_result('global')['status'], 'recorded_unverified')
        self.complete('directing', ['SHOT'])
        self.assertIn('unanswered_questions_or_settings', self.stage_result('directing')['problems'])
        review['questions'][0].update(status='answered', answer='fictional answer', answer_source='fictional reply')
        self.assertEqual(self.stage_result('global')['status'], 'stale')
        self.assertEqual(self.stage_result('directing')['status'], 'blocked_order')

    def test_missing_item_needs_a_question_and_answer_source(self):
        stage = self.complete('global', ['BRIEF'], outcome='unverified', reason='Identity is unclear')
        stage['checks'].append({'name': 'identity', 'result': 'missing', 'observation': 'unclear', 'question_id': 'Q9'})
        stage['basis'] = self.stage_result('global')['basis']
        self.assertIn('missing_question:identity', self.stage_result('global')['problems'])
        self.state['review_round']['questions'].append({'id': 'Q9', 'topic': 'identity', 'question': 'Who?',
                                                        'stages': ['global'], 'status': 'answered', 'answer': 'A'})
        with self.assertRaisesRegex(ValueError, 'actual source'):
            self.result()

    def test_change_to_design_invalidates_its_dependents_not_global(self):
        self.complete_all()
        (self.root / 'shot.txt').write_text('Changed shot design', encoding='utf-8')
        self.assertEqual(self.stage_result('global')['status'], 'recorded_pass')
        self.assertEqual(self.stage_result('directing')['status'], 'stale')
        self.assertEqual(self.stage_result('prompts')['status'], 'blocked_order')

    def test_asset_change_keeps_earlier_stages_current(self):
        self.complete_all()
        (self.root / 'image.png').write_bytes(b'a different fictional version')
        self.assertEqual(self.stage_result('prompts')['status'], 'recorded_pass')
        self.assertEqual(self.stage_result('assets')['status'], 'stale')
        self.assertEqual(self.stage_result('deliverables')['status'], 'blocked_order')

    def test_specific_block_can_be_reported_and_independent_stage_checked(self):
        self.complete('global', ['BRIEF'])
        self.complete('directing', ['SHOT'], outcome='failed', blocks=['prompts'])
        self.complete('prompts', [], outcome='blocked', reason='Director decision blocks this check')
        self.complete('assets', ['IMAGE'])
        self.assertEqual(self.stage_result('prompts')['status'], 'recorded_blocked')
        self.assertEqual(self.stage_result('assets')['status'], 'recorded_pass')
        self.assertEqual(self.result()['next_stage'], 'deliverables')

    def test_final_report_requires_own_reading_evidence(self):
        self.complete_all()
        stage = self.state['review_round']['stages'][4]
        stage['evidence'] = stage['evidence'][:-1]
        stage['basis'] = self.stage_result('deliverables')['basis']
        self.assertIn('report_self_check_missing', self.stage_result('deliverables')['problems'])
        self.assertEqual(self.stage_result('deliverables')['status'], 'needs_evidence')

    def test_final_failed_report_still_requires_own_reading_evidence(self):
        for sid, inputs in [('global', ['BRIEF']), ('directing', ['SHOT']), ('prompts', ['PROMPT']), ('assets', ['IMAGE'])]:
            self.complete(sid, inputs)
        stage = self.complete('deliverables', ['DELIVERABLE'], outcome='failed')
        self.assertIn('report_self_check_missing', self.stage_result('deliverables')['problems'])
        self.assertEqual(self.stage_result('deliverables')['status'], 'needs_evidence')
        stage['evidence'].append(self.evidence('REPORT_DELIVERABLES'))
        stage['basis'] = self.stage_result('deliverables')['basis']
        self.assertEqual(self.stage_result('deliverables')['status'], 'recorded_failed')

    def test_failed_stage_requires_real_observation_record_and_issue(self):
        stage = self.complete('global', ['BRIEF'], outcome='failed', evidence=[])
        self.assertIn('failure_needs_evidence_and_issue', self.stage_result('global')['problems'])
        stage['evidence'] = [self.evidence('BRIEF')]
        stage['checks'][0]['result'] = 'checked'
        stage['basis'] = self.stage_result('global')['basis']
        self.assertIn('failure_needs_evidence_and_issue', self.stage_result('global')['problems'])

    def test_resume_is_read_only_and_does_not_change_existing_state(self):
        self.complete('global', ['BRIEF'])
        before = copy.deepcopy(self.state)
        original = self.spec.read_bytes()
        self.assertEqual(self.result()['next_stage'], 'directing')
        self.assertEqual(self.state, before)
        self.assertEqual(self.spec.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
