"""Real FFmpeg/Pillow integration against synthetic media; no external models."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'review_boards.py'


def run_checked(argv):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace'))
    return result.stdout


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires installed ffmpeg and ffprobe')
class ReviewBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='kpl-board-suite-')
        cls.root = Path(cls.temp.name)
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                     'testsrc2=size=320x180:rate=10:duration=1.5', '-c:v', 'libx264',
                     '-pix_fmt', 'yuv420p', str(cls.root / 'cfr.mp4')])
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(cls.root / 'cfr.mp4'),
                     '-vf', 'select=eq(n\\,0)+eq(n\\,1)+eq(n\\,4)+eq(n\\,8)+eq(n\\,12)',
                     '-fps_mode', 'vfr', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.root / 'vfr.mp4')])
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-itsoffset', '1.2', '-i',
                     str(cls.root / 'cfr.mp4'), '-c', 'copy', str(cls.root / 'shifted.mp4')])
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                     'color=blue:s=320x180:r=10:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.root / 'static.mp4')])
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                     'color=green:s=160x90:r=20:d=0.1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.root / 'short.mp4')])
        frames = cls.root / 'burst-frames'
        frames.mkdir()
        for n in range(20):
            image = Image.new('RGB', (160, 90), (32, 32, 32))
            if n == 7:
                ImageDraw.Draw(image).rectangle((24, 18, 136, 72), fill=(240, 240, 240))
            image.save(frames / f'{n:03d}.png')
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-framerate', '10', '-i', str(frames / '%03d.png'),
                     '-c:v', 'libx264rgb', '-crf', '0', '-pix_fmt', 'rgb24', str(cls.root / 'burst.mp4')])
        for name, size in [('横画幅.png', (320, 100)), ('竖画幅.png', (100, 320))]:
            image = Image.new('RGB', size, '#e8b944')
            ImageDraw.Draw(image).rectangle((10, 10, size[0] - 11, size[1] - 11), fill='#18678d')
            image.save(cls.root / name)
        pan = Image.new('RGB', (640, 180), '#303030')
        pan_draw = ImageDraw.Draw(pan)
        for x in range(0, 640, 50):
            pan_draw.rectangle((x, 0, x + 24, 179), fill='#ededed')
        pan.save(cls.root / 'pan-source.png')
        run_checked(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-loop', '1', '-framerate', '10',
                     '-i', str(cls.root / 'pan-source.png'), '-vf', 'crop=320:180:x=n*16:y=0',
                     '-frames:v', '10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.root / 'camera-pan.mp4')])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp(prefix='case-', dir=self.root))
        self.addCleanup(shutil.rmtree, self.run_dir, True)

    def manifest(self, shots, **extra):
        path = self.run_dir / 'shots.json'
        path.write_text(json.dumps({'version': 1, 'shots': shots, **extra}, ensure_ascii=False), encoding='utf-8')
        return path

    def invoke(self, *args, code=0, env=None):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(SCRIPT), *map(str, args)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', env=env, timeout=120)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout if result.returncode == 0 or result.stdout else result.stderr)

    def board(self, mode, shots, *args, **extra):
        source = self.manifest(shots, **extra)
        out = self.run_dir / ('output-' + mode)
        response = self.invoke(mode, '--manifest', source, '--out', out, *args)
        return response, json.loads((out / 'index.json').read_text(encoding='utf-8')), out

    def test_sequence_order_pagination_chinese_and_proportional_timeline(self):
        shots = [{'id': '镜头' + ('长' * (60 if i == 0 else 20)) + str(i), 'section': '序章' if i < 5 else '尾声',
                  'media': str(self.root / ('横画幅.png' if i % 2 else '竖画幅.png')),
                  'duration': 1 if i % 2 == 0 else 2, 'version': '版本甲'} for i in range(12)]
        response, index, out = self.board('sequence', shots, '--page-size', '4')
        self.assertEqual(response['pages'], 4)
        self.assertEqual([c['shot_id'] for c in index['cells']], [s['id'] for s in shots])
        self.assertTrue(all(c['image_rendered'] for c in index['cells']))
        self.assertIn(shots[0]['id'], ''.join(index['cells'][0]['labels']))
        bars = index['pages'][0]['timeline_bars']
        self.assertAlmostEqual((bars[1]['x_end'] - bars[1]['x_start']) /
                               (bars[0]['x_end'] - bars[0]['x_start']), 2, delta=.01)
        self.assertEqual(index['review_status'], 'unverified')
        self.assertTrue((out / 'sequence-004.png').is_file())

    def test_thumbnail_fits_landscape_and_portrait_without_cropping(self):
        shots = [{'id': 'A', 'media': str(self.root / '横画幅.png'), 'duration': 1},
                 {'id': 'B', 'media': str(self.root / '竖画幅.png'), 'duration': 1}]
        _, index, out = self.board('sequence', shots)
        page = Image.open(out / 'sequence-001.png').convert('RGB')
        for cell, dimensions in zip(index['cells'], [(320, 100), (100, 320)]):
            x, y, w, h = cell['image_box']
            scale = min(w / dimensions[0], h / dimensions[1])
            fitted_w, fitted_h = round(dimensions[0] * scale), round(dimensions[1] * scale)
            left, top = x + (w - fitted_w) // 2, y + (h - fitted_h) // 2
            self.assertEqual(page.getpixel((left + fitted_w // 2, top + 2)), (232, 185, 68))
            self.assertEqual(page.getpixel((left + 2, top + fitted_h // 2)), (232, 185, 68))

    def test_labels_drawn_in_pixels_match_index_for_id_and_seconds(self):
        shots = [{'id': 'S001', 'media': str(self.root / 'cfr.mp4'), 'in': .2, 'out': 1.2,
                  'duration': 1, 'timeline_start': 0, 'version': 'V1'}]
        _, index, out = self.board('sequence', shots)
        cell = index['cells'][0]
        self.assertEqual(cell['frame_number'], 7)
        self.assertEqual(cell['actual_seconds'], .7)
        self.assertIn('源相对 0.700秒', cell['labels'][1])
        self.assertNotIn('帧 #', ''.join(cell['labels']))
        page = Image.open(out / 'sequence-001.png').convert('RGB')
        x, y, _, _ = cell['image_box']
        for label, yy, size, color in [(cell['labels'][0], y + 206, 20, '#ecf2f5'),
                                       (cell['labels'][1], y + 232, 16, '#afc2cd')]:
            expected = Image.new('RGB', (448, size + 4), '#202b36')
            ImageDraw.Draw(expected).text((0, 0), label, font=ImageFont.truetype(index['parameters']['font'], size), fill=color)
            actual = page.crop((x + 4, yy, x + 452, yy + size + 4))
            self.assertIsNone(ImageChops.difference(actual, expected).getbbox())

    def test_cut_uses_actual_adopted_window_and_correct_endpoints(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'cfr.mp4'), 'in': .15, 'out': .75, 'duration': .6},
                 {'id': 'S02', 'media': str(self.root / 'cfr.mp4'), 'in': .05, 'out': .65, 'duration': .6}]
        _, index, _ = self.board('cut', shots, '--left', 'S01', '--right', 'S02', '--frames', '3', '--cut-type', '连续')
        left = [c for c in index['cells'] if 'left_tail' in c['roles']]
        right = [c for c in index['cells'] if 'right_head' in c['roles']]
        self.assertEqual([c['frame_number'] for c in left], [5, 6, 7])
        self.assertEqual([c['frame_number'] for c in right], [0, 1, 2])
        self.assertEqual([c['actual_seconds'] for c in left], [.5, .6, .7])
        self.assertEqual(right[0]['visible_interval'], {'start': .05, 'end_exclusive': .1})
        self.assertEqual(index['cut']['timeline_seconds'], .6)
        self.assertEqual(index['cut']['verdict'], 'unverified')
        self.assertEqual(left[-1]['source_window'], {'in': .15, 'out_exclusive': .75})

    def test_cut_samples_by_requested_seconds_using_decoded_timestamps(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'cfr.mp4'), 'in': .15, 'out': .75, 'duration': .6},
                 {'id': 'S02', 'media': str(self.root / 'cfr.mp4'), 'in': .05, 'out': .65, 'duration': .6}]
        _, index, _ = self.board('cut', shots, '--left', 'S01', '--right', 'S02', '--samples', '3', '--interval', '.2')
        left = [c for c in index['cells'] if 'left_tail' in c['roles']]
        right = [c for c in index['cells'] if 'right_head' in c['roles']]
        self.assertEqual([c['actual_seconds'] for c in left], [.3, .5, .7])
        self.assertEqual([c['actual_seconds'] for c in right], [0, .2, .4])
        self.assertEqual(index['cut']['requested_interval_seconds'], .2)
        self.assertIsNone(index['cut']['frame_stride'])
        self.assertTrue(all('帧 #' not in ''.join(c['labels']) for c in left + right))

    def test_short_clip_and_still_cut_have_no_fabricated_frames(self):
        shots = [{'id': '短镜', 'media': str(self.root / 'short.mp4'), 'duration': .1},
                 {'id': '静帧', 'media': str(self.root / '竖画幅.png'), 'duration': .5}]
        _, index, _ = self.board('cut', shots, '--left', '短镜', '--right', '静帧', '--frames', '3')
        left = [c for c in index['cells'] if 'left_tail' in c['roles']]
        right = [c for c in index['cells'] if 'right_head' in c['roles']]
        self.assertEqual([c['frame_number'] for c in left], [0, 1])
        self.assertEqual(len(right), 1)
        self.assertEqual(right[0]['kind'], 'image')
        self.assertIsNone(right[0]['frame_number'])
        self.assertTrue(any(c['kind'] == 'padding' for c in index['cells']))

    def test_motion_keeps_baselines_and_detects_one_frame_burst(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'burst.mp4'), 'duration': 2}]
        _, index, _ = self.board('motion', shots, '--shot', 'S01', '--interval', '.5')
        fixed = {c['frame_number'] for c in index['cells'] if 'fixed_interval' in c['roles']}
        self.assertTrue({0, 5, 10, 15}.issubset(fixed))
        self.assertIn(19, {c['frame_number'] for c in index['cells'] if 'last' in c['roles']})
        candidates = {c['frame_number'] for c in index['cells'] if 'motion_candidate' in c['roles']}
        self.assertTrue(candidates & {7, 8}, candidates)
        self.assertIn('不是语义动作识别', ' '.join(index['limitations']))
        self.assertEqual(index['review_status'], 'unverified')

    def test_static_video_retains_fixed_samples_without_fabricated_motion(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'static.mp4'), 'duration': 1}]
        _, index, _ = self.board('motion', shots, '--shot', 'S01', '--interval', '.25')
        self.assertFalse(any('motion_candidate' in c['roles'] for c in index['cells']))
        self.assertEqual(index['cells'][0]['frame_number'], 0)
        self.assertEqual(index['cells'][-1]['frame_number'], 9)
        self.assertGreaterEqual(len(index['cells']), 5)

    def test_camera_pan_is_only_a_visual_change_candidate(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'camera-pan.mp4'), 'duration': 1}]
        _, index, _ = self.board('motion', shots, '--shot', 'S01', '--interval', '.5')
        self.assertTrue(any('motion_candidate' in c['roles'] for c in index['cells']))
        self.assertIn('镜头运动', ' '.join(index['limitations']))
        self.assertEqual(index['review_status'], 'unverified')

    def test_nonzero_start_pts_is_reported_without_using_average_fps(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'shifted.mp4'), 'duration': 1}]
        _, index, _ = self.board('motion', shots, '--shot', 'S01', '--interval', '.5', '--candidates', '0')
        self.assertGreater(index['sources'][0]['first_pts'], 0)
        first = index['cells'][0]
        self.assertEqual(first['frame_number'], 0)
        self.assertEqual(first['actual_seconds'], 0)
        self.assertGreater(float(first['pts'] * Fraction(first['time_base'])), 1)
        self.assertTrue(any('源相对 0.000秒' in label for label in first['labels']))

    def test_vfr_uses_decoded_presentation_times_not_average_fps(self):
        probe = json.loads(run_checked(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
            '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(self.root / 'vfr.mp4')]))
        times = [float(f['best_effort_timestamp_time']) for f in probe['frames']]
        self.assertGreater(len(set(round(times[i+1] - times[i], 4) for i in range(len(times)-1))), 1)
        shots = [{'id': 'S01', 'media': str(self.root / 'vfr.mp4'), 'in': .15, 'out': 1.15, 'duration': 1}]
        _, index, _ = self.board('motion', shots, '--shot', 'S01', '--interval', '.2', '--candidates', '0')
        for cell in index['cells']:
            self.assertAlmostEqual(cell['actual_seconds'], times[cell['frame_number']] - times[0], places=5)
            self.assertGreaterEqual(cell['visible_interval']['start'], .15)
            self.assertLessEqual(cell['visible_interval']['end_exclusive'], 1.15)
            self.assertAlmostEqual(float((cell['pts'] - index['sources'][0]['first_pts']) * Fraction(cell['time_base'])),
                                   cell['actual_seconds'], places=7)
        self.assertFalse(any('motion_candidate' in c['roles'] for c in index['cells']))

    def test_missing_and_corrupt_sources_are_explicit_and_partial(self):
        bad = self.run_dir / 'bad.png'
        bad.write_bytes(b'not an image')
        shots = [{'id': '丢失', 'media': str(self.run_dir / 'missing.png'), 'duration': 1},
                 {'id': '损坏', 'media': str(bad), 'duration': 1}]
        response, index, _ = self.board('sequence', shots)
        self.assertEqual(response['status'], 'partial')
        self.assertEqual([s['status'] for s in index['sources']], ['missing', 'invalid'])
        self.assertTrue(all(not c['image_rendered'] for c in index['cells']))
        self.assertTrue(all(any('注意' in line for line in c['labels']) for c in index['cells']))

    def test_missing_ffmpeg_does_not_prevent_image_board(self):
        shots = [{'id': '图片', 'media': str(self.root / '横画幅.png'), 'duration': 1},
                 {'id': '视频', 'media': str(self.root / 'cfr.mp4'), 'duration': 1}]
        manifest = self.manifest(shots)
        out = self.run_dir / 'no-ffmpeg'
        env = dict(os.environ)
        win_dir = os.environ.get('WINDIR')
        env['PATH'] = str(Path(sys.executable).parent) + (os.pathsep + str(Path(win_dir) / 'System32') if win_dir else '')
        response = self.invoke('sequence', '--manifest', manifest, '--out', out, env=env)
        index = json.loads((out / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual(response['status'], 'partial')
        self.assertTrue(index['cells'][0]['image_rendered'])
        self.assertEqual(index['sources'][1]['status'], 'invalid')
        self.assertIn('ffprobe', index['sources'][1]['error'])

    def test_manifest_version_mismatch_is_visible_and_stale(self):
        shots = [{'id': 'S01', 'media': str(self.root / '横画幅.png'), 'duration': 1, 'sha256': '0' * 64}]
        response, index, out = self.board('sequence', shots)
        self.assertEqual(response['status'], 'partial')
        self.assertEqual(index['sources'][0]['status'], 'version_mismatch')
        self.assertTrue(index['cells'][0]['image_rendered'])
        self.assertTrue(any('不符' in text for text in index['cells'][0]['labels']))
        self.assertTrue(self.invoke('verify', out / 'index.json', code=1)['stale'])

    def test_verify_detects_changed_sources_spec_manifest_and_pages(self):
        image = self.run_dir / 'local.png'
        shutil.copy2(self.root / '横画幅.png', image)
        spec = self.run_dir / 'creative-spec.md'
        spec.write_text('fixture spec', encoding='utf-8')
        shots = [{'id': 'S01', 'media': str(image), 'duration': 1}]
        _, index, out = self.board('sequence', shots, spec='creative-spec.md')
        self.assertFalse(self.invoke('verify', out / 'index.json')['stale'])
        image.write_bytes(b'changed')
        result = self.invoke('verify', out / 'index.json', code=1)
        self.assertTrue(result['stale'])
        self.assertIn('source', [i['kind'] for i in result['issues']])
        spec.write_text('changed spec', encoding='utf-8')
        (out / 'sequence-001.png').write_bytes(b'changed board')
        result = self.invoke('verify', out / 'index.json', code=1)
        self.assertTrue({'source', 'spec', 'page'}.issubset({i['kind'] for i in result['issues']}))

    def test_output_safety_refuses_existing_and_nested_source_paths(self):
        shot = [{'id': 'S01', 'media': str(self.root / '横画幅.png'), 'duration': 1}]
        manifest = self.manifest(shot)
        out = self.run_dir / 'already'
        out.mkdir()
        sentinel = out / 'keep.txt'
        sentinel.write_text('keep', encoding='utf-8')
        self.invoke('sequence', '--manifest', manifest, '--out', out, code=2)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        future = self.run_dir / 'future'
        manifest = self.manifest([{'id': 'S01', 'media': str(future / 'planned.png'), 'duration': 1}])
        self.invoke('sequence', '--manifest', manifest, '--out', future, code=2)
        self.assertFalse(future.exists())

    def test_invalid_window_and_nonadjacent_cut_are_rejected(self):
        shots = [{'id': 'S01', 'media': str(self.root / 'cfr.mp4'), 'in': 0, 'out': 1, 'duration': .5}]
        manifest = self.manifest(shots)
        self.invoke('sequence', '--manifest', manifest, '--out', self.run_dir / 'bad-window', code=2)
        shots = [{'id': f'S0{i}', 'media': str(self.root / 'cfr.mp4'), 'duration': .5} for i in (1, 2, 3)]
        manifest = self.manifest(shots)
        self.invoke('cut', '--manifest', manifest, '--left', 'S01', '--right', 'S03', '--out', self.run_dir / 'bad-cut', code=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
