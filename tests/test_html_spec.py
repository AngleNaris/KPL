"""Offline HTML construction/parser tests; these do not launch a browser."""
import copy
import importlib.util
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
loader = importlib.util.spec_from_file_location('html_spec_fixture', SCRIPTS / 'html_spec.py')
html_spec = importlib.util.module_from_spec(loader)
loader.loader.exec_module(html_spec)
loader = importlib.util.spec_from_file_location('html_kpl_checker', SCRIPTS / 'kpl_check.py')
kpl = importlib.util.module_from_spec(loader)
loader.loader.exec_module(kpl)


def shot():
    return {'id':'stable-shot-1', 'number':'S001', 'duration_seconds':'1.9',
            'thumbnail':'../05_参考资产/01_参考画面_v001_draft.png', 'content':'店员回头。',
            'description':'@{店员} 在柜台后回头。', 'shot_scale':'近景', 'camera_motion':'固定',
            'prompt':'{镜头:近景}，{动态:@{店员} 回头}', 'video_prompt':'{动态:@{店员} 慢慢回头}', 'mark':'wip',
            'layout_camera':{'type':'linear', 'start_crop':[0, 0, 1, 1], 'end_crop':[0.1, 0.1, 0.9, 0.9]}}


def asset():
    return {'id':'A01', 'name':'店员', 'path':'../05_参考资产/02_店员_idA01_v001_draft.png'}


class TagCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = 0
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.scripts += 1


class HtmlSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kpl-html-spec-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '规格.html'
        self.model = html_spec.new_model('合成项目')
        self.model['shots'].append(shot())
        self.model['assets'].append(asset())

    def test_user_text_is_escaped_and_long_chinese_round_trips(self):
        payload = '中文\n</script><script>alert(1)</script><img src=x onerror=alert(1)>&' + '长句。' * 3000
        self.model['shots'][0]['prompt'] = payload
        output = html_spec.build(self.model)
        self.path.write_text(output, encoding='utf-8')
        counter = TagCounter(); counter.feed(output)
        self.assertEqual(counter.scripts, 2)
        self.assertEqual(html_spec.load(self.path)['shots'][0]['prompt'], payload)
        self.assertIn('&lt;/script&gt;', output)
        visible = kpl.read_document_text(self.path)
        self.assertIn(payload, visible)
        self.assertNotIn('Offline editor. A file handle', visible)
        self.assertIn('white-space:pre-wrap', output)

    def test_input_validation_and_no_path_escape(self):
        for duration in ('-1', '0', '1e3', 'NaN'):
            changed = copy.deepcopy(self.model); changed['shots'][0]['duration_seconds'] = duration
            with self.assertRaises(ValueError): html_spec.build(changed)
        for thumb in ('../../secret.png', '../05_参考资产/../x.png', 'https://example.com/x.png'):
            changed = copy.deepcopy(self.model); changed['shots'][0]['thumbnail'] = thumb
            with self.assertRaises(ValueError): html_spec.build(changed)
        changed = copy.deepcopy(self.model); changed['shots'].append(copy.deepcopy(changed['shots'][0]))
        with self.assertRaises(ValueError): html_spec.build(changed)

    def test_v2_contract_is_exact(self):
        self.assertEqual(html_spec.validate(copy.deepcopy(self.model))['schema'], 'kpl-user-spec-v2')
        cases = [
            lambda m: m['shots'][0].update(action='旧字段'),
            lambda m: m['shots'][0].pop('mark'),
            lambda m: m['shots'][0].update(mark='urgent'),
            lambda m: m.update(extra='x'),
            lambda m: m.update(schema='kpl-user-spec-v1'),
            lambda m: m['assets'].append(asset()),
            lambda m: m['assets'][0].update(path='../../secret.png'),
            lambda m: m['assets'][0].update(id='A 01'),
            lambda m: m['assets'][0].update(name='  '),
            lambda m: m['assets'][0].update(note='x'),
            lambda m: m['shots'][0].pop('video_prompt'),
            lambda m: m['shots'][0].pop('layout_camera'),
            lambda m: m['shots'][0].update(layout_camera='推近'),
            lambda m: m['shots'][0].update(layout_camera={'type':'static', 'start_crop':[0, 0, 1, 1], 'end_crop':[0, 0, 0.5, 0.5]}),
            lambda m: m['shots'][0].update(layout_camera={'type':'linear', 'start_crop':[0, 0, 1.2, 1], 'end_crop':[0, 0, 1, 1]}),
            lambda m: m['shots'][0].update(layout_camera={'type':'linear', 'start_crop':[0.5, 0, 0.5, 1], 'end_crop':[0, 0, 1, 1]}),
            lambda m: m['shots'][0].update(layout_camera={'type':'zoom', 'start_crop':[0, 0, 1, 1], 'end_crop':[0, 0, 1, 1]}),
            lambda m: m.update(layout_aspect='16x9'),
            lambda m: m.update(layout_aspect='0:9'),
            lambda m: m.pop('layout_aspect'),
        ]
        for mutate in cases:
            changed = copy.deepcopy(self.model); mutate(changed)
            with self.assertRaises(ValueError): html_spec.validate(changed)
        for camera in (None, {'type':'static', 'start_crop':[0.1, 0.1, 0.9, 0.9], 'end_crop':[0.1, 0.1, 0.9, 0.9]}):
            changed = copy.deepcopy(self.model); changed['shots'][0]['layout_camera'] = camera; changed['layout_aspect'] = '2.39:1'
            html_spec.validate(changed)

    def test_static_view_shows_v2_fields(self):
        output = html_spec.build(self.model)
        for text in ('一句话内容', '店员回头。', '近景', '进行中', '详细描述', '图片生成提示词', '视频提示词', '慢慢回头', 'A01'):
            self.assertIn(text, output)

    def test_static_view_hides_segment_markup(self):
        view = html_spec.static_view(self.model)
        self.assertIn('近景，@{店员} 回头', view)
        self.assertNotIn('{镜头:', view)
        self.assertEqual(html_spec.plain_text('{风格:暖光 @{A01}}，未标记{其他:x}'), '暖光 @{A01}，未标记{其他:x}')

    def test_static_text_excludes_hidden_elements_and_scripts(self):
        self.path.write_text('<html><body><p>可见正文</p><div hidden>隐藏证据</div>'
                             '<script>伪造证据</script><p>公开 &amp; 文字</p></body></html>', encoding='utf-8')
        text = kpl.read_document_text(self.path)
        self.assertIn('可见正文', text)
        self.assertIn('公开 & 文字', text)
        self.assertNotIn('隐藏证据', text)
        self.assertNotIn('伪造证据', text)

    def test_tampered_scripts_do_not_pass_current_template_check(self):
        content = html_spec.build(self.model)
        self.path.write_text(content.replace('Offline editor. A file handle', 'changed application'), encoding='utf-8')
        with self.assertRaises(ValueError): html_spec.load(self.path)
        self.assertEqual(html_spec.load(self.path, require_current=False)['document_id'], self.model['document_id'])
        self.path.write_text(content.replace('<body>', '<body onload="alert(1)">'), encoding='utf-8')
        with self.assertRaises(ValueError): html_spec.load(self.path, require_current=False)

    def test_duplicate_json_keys_rejected(self):
        content = html_spec.build(self.model)
        content = content.replace('"schema": "kpl-user-spec-v2",',
                                  '"schema": "kpl-user-spec-v2", "schema": "kpl-user-spec-v2",', 1)
        self.path.write_text(content, encoding='utf-8')
        with self.assertRaises(ValueError): html_spec.load(self.path)


if __name__ == '__main__':
    unittest.main()
