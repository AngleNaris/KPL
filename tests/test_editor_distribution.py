"""Synthetic checks for the relocatable native editor distribution."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import editor_bundle
import editor_ready


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kpl-editor-bundle-')
        self.addCleanup(self.tmp.cleanup)
        self.copy = Path(self.tmp.name) / 'skill'
        for name in editor_bundle.INPUTS:
            dest = self.copy / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        self.bundle = self.copy / 'native-prototype/bin/win-x64'
        shutil.copytree(ROOT / 'native-prototype/bin/win-x64', self.bundle)
        self.root_patch = mock.patch.object(editor_bundle, 'ROOT', self.copy)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_packaged_inputs_and_program_match(self):
        result = editor_bundle.verify(self.bundle)
        self.assertEqual(result['status'], 'matching', result)
        self.assertEqual(result['document_schema'], 'kpl-user-spec-v2')
        self.assertEqual(result['sdk'], 'Microsoft.Web.WebView2 1.0.3179.45')

    def test_source_change_and_binary_change_are_distinct(self):
        source = self.copy / 'native-prototype/src/Host.cs'
        source.write_bytes(source.read_bytes() + b'\n// synthetic change\n')
        result = editor_bundle.verify(self.bundle)
        self.assertEqual(result['status'], 'changed')
        self.assertEqual(result['issues'], [{'kind': 'source', 'path': 'native-prototype/src/Host.cs'}])
        binary = self.bundle / 'WebView2Loader.dll'
        binary.write_bytes(binary.read_bytes() + b'\0')
        self.assertIn({'kind': 'binary', 'path': 'WebView2Loader.dll'},
                      editor_bundle.verify(self.bundle)['issues'])

    def test_missing_component_and_incomplete_manifest_are_rejected(self):
        (self.bundle / 'Microsoft.Web.WebView2.Core.dll').unlink()
        self.assertIn({'kind': 'binary', 'path': 'Microsoft.Web.WebView2.Core.dll'},
                      editor_bundle.verify(self.bundle)['issues'])
        path = self.bundle / 'manifest.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        del data['files_sha256']['WebView2Loader.dll']
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(editor_bundle.verify(self.bundle)['status'], 'invalid_manifest')

    def test_input_changing_during_build_prevents_manifest(self):
        inputs = editor_bundle.input_hashes()
        source = self.copy / 'native-prototype/ui/app.js'
        source.write_bytes(source.read_bytes() + b'\n// changed while building\n')
        with self.assertRaises(RuntimeError):
            editor_bundle.write_manifest(self.bundle, inputs, 'test')


class PackageReadinessTests(unittest.TestCase):
    def test_default_editor_is_relative_to_this_skill(self):
        self.assertEqual(editor_ready.PACKAGED_EXE,
                         ROOT / 'native-prototype/bin/win-x64/KPL规格编辑器.exe')
        with mock.patch.object(editor_ready, 'check', return_value={'status': 'ready'}) as check:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(editor_ready.main(['check', '--desktop-account', r'PC\user']), 0)
            self.assertEqual(check.call_args.args[0], editor_ready.PACKAGED_EXE)

    def test_runtime_detection_handles_user_or_machine_registration_and_missing_values(self):
        for versions, expected in [
            ([None, '', '0.0.0.0'], False),
            ([None, None, '156.0.4314.50'], True),
            (['156.0.4314.50', None, None], True),
            (['0.0.0.0', 'invalid', ''], False),
        ]:
            with self.subTest(versions=versions):
                self.assertEqual(editor_ready.runtime_registered(versions), expected)


if __name__ == '__main__':
    unittest.main()
