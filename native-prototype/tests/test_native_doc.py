from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_doc', HERE / 'native_doc.py')
native_doc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_doc)


class NativeDocTests(unittest.TestCase):
    def test_roundtrip_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            doc = root / '合成.kpldoc'; html = root / '合成.html'; doc2 = root / '副本.kpldoc'
            native_doc.main(['new', str(doc), '--title', '中文 1.9 秒 </script>'])
            native_doc.main(['to-html', str(doc), str(html)])
            self.assertEqual(native_doc.html_spec.load(html)['title'], '中文 1.9 秒 </script>')
            self.assertIn('type="application/x-kpl-inert"', html.read_text(encoding='utf-8'))
            self.assertIn('不会自动同步原规格', html.read_text(encoding='utf-8'))
            native_doc.main(['from-html', str(html), str(doc2)])
            self.assertEqual(native_doc.read_doc(doc2), native_doc.read_doc(doc))
            original = doc.read_bytes()
            with self.assertRaises(FileExistsError): native_doc.main(['new', str(doc), '--title', '不可覆盖'])
            self.assertEqual(doc.read_bytes(), original)

    def test_v2_fields_round_trip_through_html(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            model = native_doc.html_spec.new_model('合成')
            model['assets'].append({'id': 'A01', 'name': '店员', 'path': '../05_参考资产/02_店员_idA01_v001_draft.png'})
            shot = native_doc.html_spec.new_shot('s1', 'S01')
            shot.update(content='店员回头。', description='@{店员} 回头。', prompt='{镜头:近景}，@{A01}', video_prompt='{动态:@{A01} 回头}',
                        mark='done', layout_camera={'type': 'linear', 'start_crop': [0, 0, 1, 1], 'end_crop': [0.1, 0.1, 0.9, 0.9]})
            model['layout_aspect'] = '9:16'
            model['shots'].append(shot)
            doc = root / '合成.kpldoc'; html = root / '合成.html'
            doc.write_text(json.dumps(model, ensure_ascii=False), encoding='utf-8')
            native_doc.main(['to-html', str(doc), str(html)])
            self.assertEqual(native_doc.html_spec.load(html), model)
            shot['action'] = '旧字段'
            doc.write_text(json.dumps(model, ensure_ascii=False), encoding='utf-8')
            with self.assertRaises(ValueError): native_doc.read_doc(doc)

    def test_rejects_duplicate_and_non_model(self):
        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / '坏.kpldoc'
            doc.write_text('{"schema":"x","schema":"y"}', encoding='utf-8')
            with self.assertRaises(ValueError): native_doc.read_doc(doc)

    def test_shipped_examples_use_current_schema_and_are_read_only_export(self):
        doc = HERE / 'examples/01_合成规格_v001_draft.kpldoc'
        html = HERE / 'examples/02_合成规格_v001_draft.html'
        model = native_doc.read_doc(doc)
        self.assertEqual(model['schema'], 'kpl-user-spec-v2')
        self.assertGreaterEqual(len(model['shots']), 3)
        self.assertEqual(native_doc.html_spec.load_document(html), model)
        self.assertTrue(native_doc.html_spec.is_view_only(html))

    def test_build_refuses_to_overwrite_a_nonempty_output(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            sentinel = folder / 'existing.exe'
            sentinel.write_bytes(b'preserve')
            loader = importlib.util.spec_from_file_location('native_build_test', HERE / 'build.py')
            build = importlib.util.module_from_spec(loader)
            loader.loader.exec_module(build)
            with self.assertRaises(SystemExit):
                build.main(['--csc', sys.executable, '--core', sys.executable, '--loader', sys.executable,
                            '--out-dir', str(folder)])
            self.assertEqual(sentinel.read_bytes(), b'preserve')


if __name__ == '__main__': unittest.main()
