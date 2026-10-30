"""Synthetic real-file tests for the selected native specification workflow."""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import html_spec
import editor_ready


def run(script, *args):
    process = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), *map(str, args)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    return process.returncode, json.loads((process.stdout or process.stderr).decode('utf-8'))


def legacy_model():
    return {'schema': 'kpl-user-spec-v1', 'document_id': 'legacy-synthetic', 'version': 'v001',
            'status': 'draft', 'confirmation': 'unconfirmed', 'revision': 1, 'title': '旧合成规格',
            'summary': '', 'global_settings': '', 'shots': [{'id': 'stable-01', 'number': 'S01',
            'name': '旧名称', 'duration_seconds': '1.9', 'thumbnail': '', 'action': '人物回头。',
            'prompt': '合成提示。', 'references': '旧参考', 'settings': '', 'acceptance': ''}]}


class NativeProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kpl-native-workflow-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / '合成项目'
        rc, data = run(SCRIPTS / 'project.py', 'init', self.root)
        self.assertEqual(rc, 0, data)
        self.spec = Path(data['spec'])
        self.relative = self.spec.relative_to(self.root).as_posix()
        self.state = self.root / '.kpl/state/project-state.json'

    def apply(self, operations, expected=None):
        patch = self.root / '.kpl/tmp/patch.json'
        patch.write_text(json.dumps({'operations': operations}, ensure_ascii=False), encoding='utf-8')
        expected = expected or hashlib.sha256(self.spec.read_bytes()).hexdigest()
        return run(SCRIPTS / 'html_spec.py', 'apply', '--project', self.root, '--spec', self.relative,
                   '--expected-sha256', expected, '--patch', patch)

    def reselect(self):
        return run(SCRIPTS / 'project.py', 'select-spec', '--project', self.root, '--spec', self.relative,
                   '--basis', '合成测试中重新核对用户修改')

    def test_native_is_single_authority_and_user_edit_stales_state(self):
        self.assertEqual(self.spec.suffix, '.kpldoc')
        self.assertEqual(len(list((self.root / '01_项目说明').glob('*'))), 1)
        self.assertEqual(run(SCRIPTS / 'project.py', 'inspect', self.root)[1]['status'], 'conforming')
        shot = html_spec.new_shot('stable-a', 'S01')
        shot.update(content='店员回头。', duration_seconds='1.9', shot_scale='近景', camera_motion='固定')
        rc, result = self.apply([{'target': 'shot', 'id': 'stable-a', 'action': 'add', 'old': None, 'new': shot}])
        self.assertEqual(rc, 0, result)
        self.assertEqual(html_spec.load_document(self.spec)['shots'][0]['content'], '店员回头。')
        rc, stale = run(SCRIPTS / 'kpl_check.py', self.spec, '--state', self.state, '--allow-root', self.root, '--review')
        self.assertEqual(rc, 1, stale)
        self.assertEqual(stale['state_binding']['status'], 'stale')
        self.assertEqual(stale['next_candidates'], [])
        self.assertEqual(self.reselect()[0], 0)
        # Explicitly reselecting a file never silently blesses the machine state.
        self.assertEqual(run(SCRIPTS / 'kpl_check.py', self.spec, '--state', self.state, '--allow-root', self.root)[0], 1)

        before = self.spec.read_bytes()
        rc, error = self.apply([{'target': 'shot', 'id': 'stable-a', 'field': 'content', 'old': '旧值', 'new': '不应写入'}])
        self.assertNotEqual(rc, 0, error)
        self.assertEqual(before, self.spec.read_bytes())
        rc, error = self.apply([{'target': 'shot', 'id': 'stable-a', 'field': 'mark', 'old': '', 'new': 'done'}])
        self.assertNotEqual(rc, 0, error)
        self.assertEqual(before, self.spec.read_bytes())

    def test_read_only_export_cannot_be_selected_as_another_authority(self):
        export = self.root / '01_项目说明/02_查看副本_v001_draft.html'
        result = subprocess.run([sys.executable, str(ROOT / 'native-prototype/native_doc.py'),
                                 'to-html', str(self.spec), str(export)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(html_spec.is_view_only(export))
        self.assertEqual(html_spec.load_document(export), html_spec.load_document(self.spec))
        rc, data = run(SCRIPTS / 'project.py', 'select-spec', '--project', self.root,
                       '--spec', export.relative_to(self.root), '--basis', '合成测试')
        self.assertNotEqual(rc, 0, data)
        self.assertIn('只读', data['error'])
        self.assertEqual(run(SCRIPTS / 'project.py', 'inspect', self.root)[1]['current_spec']['path'], self.relative)

    @unittest.skipUnless(os.name == 'nt' and Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe').is_file(),
                         'needs existing Windows C# compiler')
    def test_native_editor_mutex_blocks_simultaneous_agent_write(self):
        compiler = r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
        probe = Path(self.tmp.name) / 'mutex-probe.exe'
        subprocess.run([compiler, '/nologo', '/out:' + str(probe),
                        str(ROOT / 'native-prototype/tests/MutexProbe.cs')], check=True, capture_output=True)
        ready = Path(self.tmp.name) / 'mutex-ready'
        holder = subprocess.Popen([str(probe), str(self.spec), str(ready)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(ready.exists(), 'C# probe did not acquire its mutex')
            before = self.spec.read_bytes()
            operation = [{'target': 'project', 'field': 'summary', 'old': '', 'new': '合成修改'}]
            rc, result = self.apply(operation)
            self.assertNotEqual(rc, 0, result)
            self.assertEqual(before, self.spec.read_bytes())
            self.assertEqual(holder.wait(timeout=10), 0)
            rc, result = self.apply(operation)
            self.assertEqual(rc, 0, result)
        finally:
            if holder.poll() is None:
                holder.terminate()
                holder.wait(timeout=5)

    def test_v1_html_and_native_remain_readable_and_unchanged(self):
        v1 = legacy_model()
        native = self.root / '01_项目说明/02_旧规格_v001_draft.kpldoc'
        native.write_text(json.dumps(v1, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(html_spec.load_document(native), v1)
        native_before = native.read_bytes()
        html = self.root / '01_项目说明/03_旧规格_v001_draft.html'
        source = html_spec.build(html_spec.new_model('合成'))
        source = html_spec.replace_marked(source, '<!--KPL_DATA_START-->', '<!--KPL_DATA_END-->',
            '<script type="application/json" id="kpl-authoritative-data">' + html_spec.json_for_script(v1) + '</script>')
        html.write_text(source, encoding='utf-8')
        before = html.read_bytes()
        self.assertEqual(html_spec.load_document(html), v1)
        self.assertIn('人物回头。', html_spec.document_text(html_spec.load_document(html)))
        rc, selected = run(SCRIPTS / 'project.py', 'select-spec', '--project', self.root,
                           '--spec', html.relative_to(self.root), '--basis', '合成旧文档')
        self.assertEqual(rc, 0, selected)
        patch = self.root / '.kpl/tmp/patch.json'
        patch.write_text(json.dumps({'operations': [{'target': 'project', 'field': 'title',
                                                     'old': '旧合成规格', 'new': '不应写入'}]}), encoding='utf-8')
        rc, _ = run(SCRIPTS / 'html_spec.py', 'apply', '--project', self.root, '--spec', html.relative_to(self.root),
                    '--expected-sha256', hashlib.sha256(before).hexdigest(), '--patch', patch)
        self.assertNotEqual(rc, 0)
        self.assertEqual(before, html.read_bytes())
        self.assertEqual(native_before, native.read_bytes())
        converted = self.root / '01_项目说明/04_转换规格_v001_draft.kpldoc'
        p = subprocess.run([sys.executable, str(ROOT / 'native-prototype/native_doc.py'), 'from-html', str(html), str(converted)],
                           capture_output=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(converted.exists())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs existing ffmpeg/ffprobe')
    def test_board_and_layout_bind_native_source_and_detect_external_edit(self):
        asset = self.root / '05_参考资产/01_参考画面_idS001_v001_draft.png'
        Image.new('RGB', (128, 72), '#cf6030').save(asset)
        manifest = self.root / '.kpl/state/synthetic.json'
        obj = {'version': 1, 'spec': os.path.relpath(self.spec, manifest.parent),
               'layout': {'width': 128, 'height': 72, 'fps': '16', 'aspect_ratio': '16:9',
                          'confirmation_ref': '仅合成测试', 'frame_policy': 'exact'},
               'shots': [{'id': 'S001', 'media': os.path.relpath(asset, manifest.parent), 'section': '合成段落',
                          'timeline_start': 0, 'in': 0, 'out': .25, 'duration': .25, 'shot_size': '中景',
                          'transition_in': 'cut', 'planned_action': '合成说明',
                          'camera': {'type': 'static', 'start_crop': [0, 0, 1, 1], 'end_crop': [0, 0, 1, 1]}}]}
        manifest.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        indexes = []
        for mode, tool in [('board', 'review_boards.py'), ('layout', 'layout_preview.py')]:
            args = [mode] + (['sequence'] if mode == 'board' else [])
            rc, report = run(SCRIPTS / 'project.py', *args, '--project', self.root, '--manifest', manifest,
                             '--number', '01', '--version', 'v001')
            self.assertEqual(rc, 0, report)
            index = self.root / report['index']
            self.assertEqual(run(SCRIPTS / tool, 'verify', index)[0], 0)
            indexes.append((tool, index))
        model = html_spec.load_document(self.spec)
        model['summary'] = '合成用户在编辑器保存的改动。'; model['revision'] += 1
        self.spec.write_text(json.dumps(model, ensure_ascii=False), encoding='utf-8')
        for tool, index in indexes:
            rc, report = run(SCRIPTS / tool, 'verify', index)
            self.assertEqual(rc, 1, report)
            self.assertIn('spec', [issue['kind'] for issue in report['issues']])
        self.assertEqual(run(SCRIPTS / 'project.py', 'inspect', self.root)[1]['status'], 'review_required')


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.exe = Path('C:/KPL/editor.exe')
        self.info = {'account': r'PC\user', 'exe': True, 'core': True, 'loader': True, 'dotnet': True,
                     'webview': True, 'user_choice': None, 'association_records': False,
                     'effective_command': None, 'inspection_error': None}

    def test_distinguishes_ready_missing_conflict_protected_and_wrong_account(self):
        account = self.info['account']
        self.assertEqual(editor_ready.classify(self.info, account, self.exe), 'needs_setup')
        ready = {**self.info, 'effective_command': '"' + str(self.exe.absolute()) + '" "%1"'}
        self.assertEqual(editor_ready.classify(ready, account, self.exe), 'ready')
        for field, status in [('exe', 'editor_missing'), ('core', 'editor_missing'), ('dotnet', 'runtime_missing'),
                              ('webview', 'runtime_missing'), ('association_records', 'association_conflict'),
                              ('user_choice', 'protected_choice')]:
            value = 'other' if field == 'user_choice' else (True if field == 'association_records' else False)
            self.assertEqual(editor_ready.classify({**self.info, field: value}, account, self.exe), status)
        self.assertEqual(editor_ready.classify(ready, r'PC\someone-else', self.exe), 'wrong_user_context')

    def test_setup_skips_ready_and_never_writes_on_conflict_or_without_authorization(self):
        for status in ('ready', 'association_conflict', 'protected_choice', 'runtime_missing', 'needs_setup'):
            with self.subTest(status=status), mock.patch.object(editor_ready, 'check', return_value={'status': status}), \
                    mock.patch.object(editor_ready.subprocess, 'run') as invoke:
                result = editor_ready.setup(self.exe, self.info['account'], None)
                invoke.assert_not_called()
                if status == 'ready': self.assertEqual(result['action'], 'already_ready_no_write')
                if status == 'needs_setup': self.assertEqual(result['status'], 'authorization_required')


CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'needs existing Windows C# compiler')
class NativeCSharpTests(unittest.TestCase):
    """编译并运行 native-prototype/tests 中的 C# 测试；只写临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kpl-cs-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def compile_and_run(self, name, sources, references, main=None, arg=None):
        exe = self.dir / (name + '.exe')
        command = [str(CSC), '/nologo', '/out:' + str(exe)] + ['/reference:' + ref for ref in references]
        if main: command.append('/main:' + main)
        compiled = subprocess.run(command + [str(ROOT / s) for s in sources], capture_output=True)
        self.assertEqual(compiled.returncode, 0, compiled.stdout.decode('gbk', 'replace'))
        return subprocess.run([str(exe), str(arg)], capture_output=True, timeout=60, cwd=self.dir)

    def test_document_session_save_conflict_and_duration_precision(self):
        doc = self.dir / 'doc'
        doc.mkdir()
        shutil.copy2(ROOT / 'native-prototype/examples/01_合成规格_v001_draft.kpldoc', doc)
        result = self.compile_and_run('SessionTests', ['native-prototype/src/DocumentSession.cs', 'native-prototype/tests/SessionTests.cs'],
                                      ['System.Web.Extensions.dll'], arg=doc)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertIn(b'PASS', result.stdout)

    def test_host_thumbnails_and_prefs_merge(self):
        core = ROOT / 'native-prototype/bin/win-x64/Microsoft.Web.WebView2.Core.dll'
        shutil.copy2(core, self.dir)
        result = self.compile_and_run('HostTests', ['native-prototype/src/Host.cs', 'native-prototype/src/DocumentSession.cs', 'native-prototype/tests/HostTests.cs'],
                                      [str(core), 'System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll'],
                                      main='HostTests', arg=self.dir / 'work')
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertIn(b'PASS', result.stdout)


if __name__ == '__main__': unittest.main()
