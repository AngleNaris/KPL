"""Real CLI checks with synthetic Chinese-named projects; no real media or models."""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
PROJECT = SCRIPTS / 'project.py'
BOARD = SCRIPTS / 'review_boards.py'
LAYOUT = SCRIPTS / 'layout_preview.py'
loader = importlib.util.spec_from_file_location('kpl_project_test', SCRIPTS / 'kpl_check.py')
kpl = importlib.util.module_from_spec(loader)
loader.loader.exec_module(kpl)


def run(script, *args):
    p = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), *map(str, args)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    text = (p.stdout or p.stderr).decode('utf-8')
    return p.returncode, json.loads(text)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kpl-project-')
        self.addCleanup(self.temp.cleanup)
        self.outer = Path(self.temp.name)
        self.root = self.outer / '合成项目'
        code, data = run(PROJECT, 'init', self.root)
        self.assertEqual(code, 0, data)
        self.spec = Path(data['spec'])

    def fixture(self):
        asset = self.root / '05_参考资产' / '01_参考画面_idS001_v001_draft.png'
        Image.new('RGB', (128, 72), '#cf6030').save(asset)
        manifest = self.root / '.kpl' / 'state' / 'synthetic.json'
        shot = {'id': 'S001', 'media': os.path.relpath(asset, manifest.parent), 'section': '合成段落',
                'timeline_start': 0, 'in': 0, 'out': .25, 'duration': .25, 'shot_size': '中景',
                'transition_in': 'cut', 'planned_action': '合成说明',
                'camera': {'type': 'static', 'start_crop': [0, 0, 1, 1], 'end_crop': [0, 0, 1, 1]}}
        obj = {'version': 1, 'spec': os.path.relpath(self.spec, manifest.parent),
               'layout': {'width': 128, 'height': 72, 'fps': '16', 'aspect_ratio': '16:9',
                          'confirmation_ref': '仅合成测试', 'frame_policy': 'exact'}, 'shots': [shot]}
        manifest.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        return asset, manifest

    def publish(self, mode, manifest, number='01', version='v001'):
        return run(PROJECT, mode, *(['sequence'] if mode == 'board' else []), '--project', self.root,
                   '--manifest', manifest, '--number', number, '--version', version)

    def test_initialization_is_ordered_and_existing_root_never_overwritten(self):
        rc, report = run(PROJECT, 'inspect', self.root)
        self.assertEqual(rc, 0, report)
        self.assertEqual(report['status'], 'conforming')
        before = self.spec.read_bytes()
        rc, error = run(PROJECT, 'init', self.root)
        self.assertNotEqual(rc, 0, error)
        self.assertEqual(before, self.spec.read_bytes())
        self.assertEqual(sorted(d.name for d in self.root.iterdir() if d.is_dir() and d.name != '.kpl'),
                         ['01_项目说明', '02_导演方案', '03_分镜设计', '04_提示词',
                          '05_参考资产', '06_审查报告', '07_预演交付'])

    def test_invalid_names_and_legacy_mapping_are_read_only(self):
        bad = self.root / '02_导演方案' / '01_不是_主名称_v1_draft.md'
        bad.write_text('fictional', encoding='utf-8')
        rc, report = run(PROJECT, 'inspect', self.root)
        self.assertEqual(rc, 0)
        self.assertIn('nonstandard_file', [x['issue'] for x in report['issues']])
        legacy = self.outer / 'old project'
        legacy.mkdir()
        original = legacy / 'Original reference v2.png'
        original.write_bytes(b'preserve original exact bytes')
        before = sorted((p.relative_to(legacy).as_posix(), p.read_bytes()) for p in legacy.rglob('*') if p.is_file())
        rc, report = run(PROJECT, 'inspect', legacy)
        self.assertEqual(rc, 0)
        self.assertEqual(report['status'], 'legacy_mapping_needed')
        self.assertEqual(report['mapping'][0]['original'], original.name)
        self.assertIsNone(report['mapping'][0]['suggested_destination'])
        after = sorted((p.relative_to(legacy).as_posix(), p.read_bytes()) for p in legacy.rglob('*') if p.is_file())
        self.assertEqual(before, after)

    def test_fixed_fields_windows_safe_and_version_sorting(self):
        p = kpl.project_paths
        valid = ['01_分镜脚本_v001_draft.md', '01_分镜脚本_v002_review.md',
                 '01_分镜脚本_v010_final.md', '01_参考画面_idS001_p002_v003_draft.png']
        invalid = ['01_分镜_脚本_v001_draft.md', '01_分镜脚本_v1_draft.md', '00_分镜_v001_draft.md',
                   '01_分镜_v000_final.md', '../01_分镜_v001_draft.md', '01_CON_v001_draft.md',
                   '01_分镜_v001_final.exe:other', '01_分镜_v001_draft.md.']
        self.assertTrue(all(p.file_parts(x) for x in valid))
        self.assertTrue(all(p.file_parts(x) is None for x in invalid))
        self.assertEqual(sorted(valid[:3]), valid[:3])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs local ffmpeg/ffprobe')
    def test_board_and_layout_use_user_files_and_machine_indexes_and_verify(self):
        asset, manifest = self.fixture()
        for mode, script in [('board', BOARD), ('layout', LAYOUT)]:
            rc, report = self.publish(mode, manifest)
            self.assertEqual(rc, 0, report)
            self.assertFalse(report['verification']['stale'])
            self.assertEqual(report['review_status'], 'unverified')
            index_path = self.root / report['index']
            self.assertTrue(index_path.is_relative_to(self.root / '.kpl' / 'index'))
            output = self.root / report['outputs'][0]
            self.assertTrue(output.exists())
            self.assertTrue(kpl.project_paths.file_parts(output.name))
            self.assertEqual(output.parent.name, '06_审查报告' if mode == 'board' else '07_预演交付')
            self.assertNotEqual(output.parent, index_path.parent)
            idx = json.loads(index_path.read_text(encoding='utf-8'))
            self.assertFalse(Path(idx['spec']['path']).is_absolute())
            self.assertFalse(Path(idx['manifest']['path']).is_absolute())
            rc, verified = run(script, 'verify', index_path)
            self.assertEqual(rc, 0, verified)
            self.assertFalse(verified['stale'])
            if mode == 'board':
                # The KPL review state can trace the visible page through a machine-only index.
                artifacts = {'PAGE': {'path': os.path.relpath(output, self.spec.parent), 'kind': 'image'},
                             'IDX': {'path': os.path.relpath(index_path, self.spec.parent), 'kind': 'document'},
                             'SRC': {'path': os.path.relpath(asset, self.spec.parent), 'kind': 'reference_image'}}
                files = {key: {'sha256': kpl.sha_file(self.spec.parent / value['path']), 'status': 'current'}
                         for key, value in artifacts.items()}
                evidence = {'artifact': 'PAGE', 'board_index': 'IDX', 'board_index_sha256': files['IDX']['sha256']}
                self.assertEqual(kpl.board_coverage(self.spec.parent, evidence, artifacts, files, ['SRC']), {'SRC'})

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs local ffmpeg/ffprobe')
    def test_spec_edit_and_explicit_version_selection_make_prior_evidence_stale(self):
        _, manifest = self.fixture()
        results = {}
        for mode in ('board', 'layout'):
            rc, results[mode] = self.publish(mode, manifest)
            self.assertEqual(rc, 0, results[mode])
        self.spec.write_text(self.spec.read_text(encoding='utf-8') + '\n合成用户修订。\n', encoding='utf-8')
        rc, report = run(PROJECT, 'inspect', self.root)
        self.assertIn('current_spec_changed_or_missing', [x['issue'] for x in report['issues']])
        rc, report = self.publish('layout', manifest, '02')
        self.assertNotEqual(rc, 0, report)
        for mode, tool in [('board', BOARD), ('layout', LAYOUT)]:
            rc, report = run(tool, 'verify', self.root / results[mode]['index'])
            self.assertEqual(rc, 1, report)
            self.assertIn('spec', [x['kind'] for x in report['issues']])
        rc, report = run(PROJECT, 'select-spec', '--project', self.root, '--spec',
                         self.spec.relative_to(self.root), '--basis', '合成用户的明确选择')
        self.assertEqual(rc, 0, report)
        rc, report = run(BOARD, 'verify', self.root / results['board']['index'])
        self.assertEqual(rc, 1, report)
        self.assertIn('current_spec', [x['kind'] for x in report['issues']])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs local ffmpeg/ffprobe')
    def test_conflict_prevents_overwrite_and_project_can_move_as_a_whole(self):
        _, manifest = self.fixture()
        rc, first = self.publish('board', manifest)
        self.assertEqual(rc, 0, first)
        rc, preview = self.publish('layout', manifest)
        self.assertEqual(rc, 0, preview)
        output = self.root / first['outputs'][0]
        original = output.read_bytes()
        rc, duplicate = self.publish('board', manifest)
        self.assertNotEqual(rc, 0, duplicate)
        self.assertEqual(output.read_bytes(), original)
        target = self.outer / '合成项目迁移测试'
        self.root.rename(target)
        rc, verified = run(BOARD, 'verify', target / first['index'])
        self.assertEqual(rc, 0, verified)
        self.assertFalse(verified['stale'])
        rc, verified = run(LAYOUT, 'verify', target / preview['index'])
        self.assertEqual(rc, 0, verified)
        self.assertFalse(verified['stale'])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs local ffmpeg/ffprobe')
    def test_rejects_bad_version_unbound_spec_and_index_path_escape(self):
        _, manifest = self.fixture()
        for version in ('v1', 'v000', 'v001_final', '../v001'):
            rc, report = self.publish('layout', manifest, version=version)
            self.assertNotEqual(rc, 0, report)
        raw = json.loads(manifest.read_text(encoding='utf-8'))
        raw['spec'] = '../../other.md'
        manifest.write_text(json.dumps(raw), encoding='utf-8')
        rc, report = self.publish('board', manifest)
        self.assertNotEqual(rc, 0, report)
        raw['spec'] = os.path.relpath(self.spec, manifest.parent)
        manifest.write_text(json.dumps(raw), encoding='utf-8')
        rc, report = self.publish('board', manifest)
        self.assertEqual(rc, 0, report)
        index = self.root / report['index']
        obj = json.loads(index.read_text(encoding='utf-8'))
        obj['project_output']['directory'] = '../outside'
        index.write_text(json.dumps(obj), encoding='utf-8')
        rc, report = run(BOARD, 'verify', index)
        self.assertNotEqual(rc, 0, report)
        self.assertFalse(report['ok'])

    def test_select_spec_rejects_escape_and_never_assumes_final_is_confirmed(self):
        rc, report = run(PROJECT, 'select-spec', '--project', self.root, '--spec',
                         '../outside.md', '--basis', 'fictional')
        self.assertNotEqual(rc, 0, report)
        final = self.spec.parent / '01_项目规格_v002_final.md'
        final.write_text('fictional', encoding='utf-8')
        rc, report = run(PROJECT, 'select-spec', '--project', self.root, '--spec',
                         final.relative_to(self.root), '--basis', '合成测试选择这个文件')
        self.assertEqual(rc, 0, report)
        self.assertIn('不表示创作确认', report['note'])

    def test_machine_state_is_separate_and_spec_change_blocks_resume(self):
        state_path = self.root / '.kpl' / 'state' / 'project-state.json'
        original = self.spec.read_bytes()
        self.assertNotIn(b'```kpl-state', original)
        obj = json.loads(state_path.read_text(encoding='utf-8'))
        obj['issues'].append({'id': 'I1', 'description': 'fictional test update'})
        state_path.write_text(json.dumps(obj), encoding='utf-8')
        self.assertEqual(self.spec.read_bytes(), original)
        rc, inspection = run(PROJECT, 'inspect', self.root)
        self.assertEqual(inspection['status'], 'conforming')
        rc, report = run(SCRIPTS / 'kpl_check.py', self.spec, '--state', state_path, '--allow-root', self.root)
        self.assertEqual(rc, 0, report)
        self.assertEqual(report['state_binding']['status'], 'current')
        self.spec.write_bytes(original + b'\nfictional change\n')
        rc, report = run(SCRIPTS / 'kpl_check.py', self.spec, '--state', state_path, '--allow-root', self.root, '--review')
        self.assertEqual(rc, 1, report)
        self.assertEqual(report['state_binding']['status'], 'stale')
        self.assertEqual(report['next_candidates'], [])
        self.assertEqual(report['review_round']['status'], 'spec_changed_requires_review')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs local ffmpeg/ffprobe')
    def test_cut_and_motion_publish_without_renaming_imported_original(self):
        _, manifest = self.fixture()
        original = self.root / '.kpl' / 'imports' / 'external Original shot v2.mp4'
        process = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                                  'testsrc2=size=128x72:rate=16:duration=0.5', '-c:v', 'libx264',
                                  '-pix_fmt', 'yuv420p', str(original)], capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        original_bytes = original.read_bytes()
        obj = json.loads(manifest.read_text(encoding='utf-8'))
        obj['shots'][0].update(media=os.path.relpath(original, manifest.parent), duration=.5, out=.5)
        second = copy.deepcopy(obj['shots'][0])
        second.update(id='S002', timeline_start=.5)
        obj['shots'].append(second)
        manifest.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        for mode, extra, number in [('cut', ['--left', 'S001', '--right', 'S002'], '02'),
                                    ('motion', ['--shot', 'S001'], '03')]:
            rc, report = run(PROJECT, 'board', mode, '--project', self.root, '--manifest', manifest,
                             '--number', number, '--version', 'v001', '--interval', '.1', *extra)
            self.assertEqual(rc, 0, report)
            self.assertFalse(report['verification']['stale'])
            index = json.loads((self.root / report['index']).read_text(encoding='utf-8'))
            self.assertIn('external Original shot v2.mp4', index['sources'][0]['original_path'])
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertTrue(kpl.project_paths.file_parts(Path(report['outputs'][0]).name))

    def test_legacy_frame_time_converts_read_only_with_explicit_rate(self):
        rc, report = run(PROJECT, 'seconds-from-frames', '--frames', '36', '--fps', '24')
        self.assertEqual(rc, 0, report)
        self.assertEqual(report['seconds'], 1.5)
        self.assertEqual(report['seconds_exact'], '3/2')
        rc, report = run(PROJECT, 'seconds-from-frames', '--frames', '24', '--fps', '24000/1001')
        self.assertEqual(rc, 0, report)
        self.assertEqual(report['seconds_exact'], '1001/1000')
        rc, report = run(PROJECT, 'seconds-from-frames', '--frames', '36', '--fps', '0')
        self.assertNotEqual(rc, 0, report)


if __name__ == '__main__':
    unittest.main()
