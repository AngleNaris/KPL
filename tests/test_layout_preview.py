"""Actual ffmpeg/Pillow integration using synthetic, non-private stills."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'layout_preview.py'


class LayoutPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kpl-layout-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        image = Image.new('RGB', (640, 360), '#e43b35')
        ImageDraw.Draw(image).rectangle((320, 0, 639, 359), fill='#2365dd')
        image.save(self.root / 'colors.png')
        Image.new('RGB', (120, 240), '#45b36e').save(self.root / 'portrait.png')
        (self.root / 'spec.md').write_text('synthetic confirmed test spec', encoding='utf-8')
        self.manifest = {
            'version': 1, 'spec': 'spec.md',
            'layout': {'width': 320, 'height': 180, 'fps': '24', 'aspect_ratio': '16:9',
                       'confirmation_ref': 'synthetic test data', 'frame_policy': 'exact'},
            'shots': [self.shot('镜头一', 'colors.png', 0, 1, 'static'),
                      self.shot('镜头二', 'colors.png', 1, 0.5, 'linear')]}
        self.path = self.root / 'manifest.json'
        self.out = self.root / 'output'

    @staticmethod
    def shot(sid, media, start, duration, camera):
        return {'id': sid, 'media': media, 'section': '合成段落', 'duration': duration,
                'timeline_start': start, 'in': 0, 'out': duration, 'shot_size': '中景',
                'transition_in': 'cut', 'planned_action': '人物计划行动，不是真实动作',
                'camera': {'type': camera, 'start_crop': [0, 0, 0.5, 0.5],
                           'end_crop': [0.5, 0, 1, 0.5] if camera == 'linear' else [0, 0, 0.5, 0.5]}}

    def write_manifest(self):
        self.path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding='utf-8')

    def run_cli(self, *args, env=None):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True, encoding='utf-8', env=env, timeout=45)
        try:
            return result.returncode, json.loads(result.stdout)
        except ValueError:
            self.fail(f'CLI produced invalid JSON: {result.stdout!r} {result.stderr!r}')

    def create(self, *args):
        self.write_manifest()
        code, output = self.run_cli('create', '--manifest', self.path, '--out', self.out, *args)
        if code:
            self.fail(f'layout create failed: {output}')
        return json.loads((self.out / 'index.json').read_text(encoding='utf-8'))

    def decode_frame(self, frame):
        output = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.out / 'layout-preview.mp4'),
                                 '-vf', f'select=eq(n\\,{frame})', '-vsync', '0', '-frames:v', '1',
                                 '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'], capture_output=True, check=True).stdout
        import io
        return Image.open(io.BytesIO(output)).convert('RGB')

    def test_frame_count_timing_silent_and_declared_motion(self):
        index = self.create()
        self.assertEqual(index['settings']['fps'], '24/1')
        self.assertEqual(index['video']['total_frames'], 36)
        self.assertEqual(index['video']['encoded_duration_seconds'], 1.5)
        self.assertEqual(index['video']['audio'], 'silent_no_audio_stream')
        self.assertEqual([(s['frame_start'], s['frame_end_exclusive']) for s in index['shots']], [(0, 24), (24, 36)])
        self.assertFalse(index['shots'][1]['performed_character_action'])
        first = self.decode_frame(24).getpixel((160, 140))
        last = self.decode_frame(35).getpixel((160, 140))
        self.assertGreater(first[0], first[2] + 80)
        self.assertGreater(last[2], last[0] + 80)
        self.assertEqual(index['review_status'], 'unverified')

    def test_static_override_preserves_first_crop(self):
        index = self.create('--static')
        self.assertEqual(index['shots'][1]['camera_applied'], 'static_override')
        self.assertEqual(index['shots'][1]['camera_declared']['type'], 'linear')
        last = self.decode_frame(35).getpixel((160, 140))
        self.assertGreater(last[0], last[2] + 80)

    def test_portrait_is_contained_without_fake_background_or_cropping(self):
        self.manifest['shots'] = [self.shot('竖屏', 'portrait.png', 0, 0.5, 'static')]
        self.manifest['shots'][0]['camera']['start_crop'] = [0, 0, 1, 1]
        self.manifest['shots'][0]['camera']['end_crop'] = [0, 0, 1, 1]
        index = self.create()
        self.assertTrue(index['shots'][0]['framing_warnings'])
        frame = self.decode_frame(0)
        self.assertLess(max(frame.getpixel((10, 140))), 20)
        center = frame.getpixel((160, 140))
        self.assertGreater(center[1], center[0] + 30)

    def test_transparent_image_uses_black_and_reports_it(self):
        image = Image.new('RGBA', (320, 180), (255, 255, 255, 0))
        ImageDraw.Draw(image).rectangle((100, 80, 220, 160), fill=(200, 20, 20, 255))
        image.save(self.root / 'alpha.png')
        self.manifest['shots'] = [self.shot('透明素材', 'alpha.png', 0, 0.5, 'static')]
        self.manifest['shots'][0]['camera']['start_crop'] = [0, 0, 1, 1]
        self.manifest['shots'][0]['camera']['end_crop'] = [0, 0, 1, 1]
        index = self.create()
        self.assertTrue(any('透明' in w for w in index['shots'][0]['framing_warnings']))
        frame = self.decode_frame(0)
        self.assertLess(max(frame.getpixel((20, 140))), 20)
        self.assertGreater(frame.getpixel((160, 140))[0], 150)

    def test_missing_settings_or_explicit_camera_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        for kind, field in [('layout', 'width'), ('layout', 'fps'), ('layout', 'aspect_ratio'),
                            ('layout', 'confirmation_ref'), ('layout', 'frame_policy'),
                            ('shot', 'in'), ('shot', 'out'), ('shot', 'camera'), ('shot', 'transition_in')]:
            self.manifest = copy.deepcopy(original)
            del (self.manifest['layout'] if kind == 'layout' else self.manifest['shots'][0])[field]
            self.write_manifest()
            code, result = self.run_cli('create', '--manifest', self.path, '--out', self.out)
            self.assertEqual(code, 2, (kind, field, result))
            self.assertFalse(self.out.exists())

    def test_invalid_aspect_crop_and_unsupported_transition_rejected(self):
        original = copy.deepcopy(self.manifest)
        for mutate in [lambda m: m['layout'].update(aspect_ratio='4:3'),
                       lambda m: m['shots'][0]['camera'].update(end_crop=[0, 0, 1.1, 1]),
                       lambda m: m['shots'][0]['camera'].update(end_crop=[0.1, 0, 0.6, 0.5]),
                       lambda m: m['shots'][0].update(transition_in='dissolve'),
                       lambda m: m['shots'][1].update(timeline_start=1.2)]:
            self.manifest = copy.deepcopy(original)
            mutate(self.manifest)
            self.write_manifest()
            code, result = self.run_cli('create', '--manifest', self.path, '--out', self.out)
            self.assertEqual(code, 2, result)
            self.assertFalse(self.out.exists())

    def test_nonframe_duration_needs_authorization_and_reports_rounding(self):
        self.manifest['shots'] = [self.shot('A', 'colors.png', 0, 0.1, 'static'),
                                  self.shot('B', 'colors.png', 0.1, 0.1, 'static'),
                                  self.shot('C', 'colors.png', 0.2, 0.1, 'static')]
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        self.manifest['layout']['frame_policy'] = 'nearest_cumulative'
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        self.manifest['layout'].update(rounding_authorized=True, rounding_ref='synthetic approved test')
        index = self.create()
        self.assertEqual([s['frame_count'] for s in index['shots']], [2, 3, 2])
        self.assertEqual(index['video']['total_frames'], 7)
        self.assertAlmostEqual(index['video']['planned_duration_seconds'], 0.3)
        self.assertAlmostEqual(index['video']['duration_error_seconds'], float(Fraction(7, 24) - Fraction(3, 10)), places=8)

    def test_seconds_are_authoritative_and_19_seconds_reports_deviation(self):
        self.manifest['shots'] = [self.shot('A', 'colors.png', 0, 1.9, 'static')]
        self.write_manifest()
        code, output = self.run_cli('create', '--manifest', self.path, '--out', self.out)
        self.assertEqual(code, 2, output)
        self.assertIn('1.900000 秒', output['error'])
        self.assertIn('1.916667 秒', output['error'])
        self.assertIn('+0.016667 秒', output['error'])
        self.assertNotIn('帧', output['error'])
        self.assertFalse(self.out.exists())
        self.manifest['layout']['frame_policy'] = 'nearest_cumulative'
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        self.manifest['layout'].update(rounding_authorized=True, rounding_ref='仅合成测试的明确授权')
        self.write_manifest()
        code, output = self.run_cli('create', '--manifest', self.path, '--out', self.out)
        self.assertEqual(code, 0, output)
        self.assertNotIn('frames', output)
        self.assertAlmostEqual(output['timing_seconds']['planned'], 1.9)
        self.assertAlmostEqual(output['timing_seconds']['actual'], 46 / 24)
        self.assertAlmostEqual(output['timing_seconds']['error'], 1 / 60)
        index = json.loads((self.out / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual(index['video']['total_frames'], 46)
        self.assertEqual(index['video']['planned_duration_exact'], '19/10')
        self.assertEqual(index['video']['encoded_duration_exact'], '23/12')
        self.assertEqual(index['video']['duration_error_exact'], '1/60')
        self.assertEqual(index['shots'][0]['display']['duration_label'], '计划 1.900秒 · 实际 1.917秒')
        self.assertEqual(self.run_cli('verify', self.out / 'index.json')[0], 0)

    def test_one_frame_motion_is_rejected_static_allowed(self):
        self.manifest['shots'] = [self.shot('A', 'colors.png', 0, '1/24', 'linear')]
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        self.manifest['shots'][0]['camera'].update(type='static', end_crop=[0, 0, 0.5, 0.5])
        index = self.create()
        self.assertEqual(index['video']['total_frames'], 1)

    def test_fractional_fps_keeps_rational_timebase(self):
        self.manifest['layout']['fps'] = '24000/1001'
        self.manifest['shots'] = [self.shot('A', 'colors.png', 0, '1001/24000', 'static')]
        index = self.create()
        self.assertEqual(index['settings']['fps'], '24000/1001')
        self.assertEqual(index['video']['total_frames'], 1)
        self.assertAlmostEqual(index['video']['encoded_duration_seconds'], 1001 / 24000, places=8)

    def test_source_spec_and_video_changes_make_index_stale(self):
        self.create()
        index_path = self.out / 'index.json'
        self.assertFalse(self.run_cli('verify', index_path)[1]['stale'])
        (self.root / 'colors.png').write_bytes(b'changed source')
        (self.root / 'spec.md').write_text('changed spec', encoding='utf-8')
        (self.out / 'layout-preview.mp4').write_bytes(b'changed output')
        code, result = self.run_cli('verify', index_path)
        self.assertEqual(code, 1)
        kinds = {i['kind'] for i in result['issues']}
        self.assertIn('spec', kinds)
        self.assertIn('video', kinds)
        self.assertTrue(any(k.startswith('source:') for k in kinds))

    def test_existing_output_and_missing_or_corrupt_images_are_safe(self):
        self.out.mkdir()
        sentinel = self.out / 'sentinel.txt'
        sentinel.write_text('keep', encoding='utf-8')
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.out = self.root / 'new-output'
        self.manifest['shots'][0]['media'] = 'missing.png'
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        (self.root / 'bad.png').write_bytes(b'not a valid image')
        self.manifest['shots'][0]['media'] = 'bad.png'
        self.write_manifest()
        self.assertEqual(self.run_cli('create', '--manifest', self.path, '--out', self.out)[0], 2)
        self.assertFalse(self.out.exists())

    def test_missing_ffmpeg_is_explicit_no_installation(self):
        self.write_manifest()
        env = dict(os.environ)
        env['PATH'] = str(self.root)
        code, result = self.run_cli('create', '--manifest', self.path, '--out', self.out, env=env)
        self.assertEqual(code, 2)
        self.assertIn('ffmpeg', result['error'])
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
