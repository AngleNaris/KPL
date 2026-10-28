#!/usr/bin/env python3
"""Create a silent still-image layout preview from an explicit KPL review manifest.

This only simulates a 2-D crop of each still. It does not animate people or
prove performance, natural action speed, depth, parallax, or final edit quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
import review_boards as boards
import project_paths

VERSION = '1.0.0'
SCHEMA = 'kpl-layout-preview-v1'
HERE = Path(__file__).resolve()
MODULE = Path(boards.__file__).resolve()


def frac(value, label, positive=False):
    value = boards.number(value, label)
    if positive and value <= 0:
        raise boards.BoardError(f'{label} 必须大于零。')
    return value


def required_text(value, label, maxlen=200):
    return boards.safe_text(value, label, maxlen)


def rect(value, label):
    if not isinstance(value, list) or len(value) != 4:
        raise boards.BoardError(f'{label} 必须是 [左, 上, 右, 下] 四个归一化坐标。')
    coords = [frac(v, label) for v in value]
    if any(v > 1 for v in coords) or coords[0] >= coords[2] or coords[1] >= coords[3]:
        raise boards.BoardError(f'{label} 必须在 0..1 内且有正面积，不能请求图片外内容。')
    return coords


def normalized(values):
    return [float(v) for v in values]


def round_half_up(value):
    return (value.numerator * 2 + value.denominator) // (2 * value.denominator)


def load_image(path):
    with Image.open(path) as source:
        rgba = ImageOps.exif_transpose(source).convert('RGBA')
        rgba.load()
    alpha = rgba.getchannel('A')
    transparent = alpha.getextrema()[0] < 255
    image = Image.new('RGB', rgba.size, 'black')
    image.paste(rgba, mask=alpha)
    rgba.close()
    return image, transparent


def validate(manifest):
    path, manifest_hash, raw, shots, spec = boards.load_manifest(manifest)
    config = raw.get('layout')
    if not isinstance(config, dict):
        raise boards.BoardError('清单缺少 layout 设置；先确认分辨率、输出帧率、画幅及时长偏差处理方式。')
    width, height = config.get('width'), config.get('height')
    if (isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int)
            or not isinstance(height, int) or width < 64 or height < 64 or width % 2 or height % 2):
        raise boards.BoardError('width 和 height 需明确为至少 64 的偶数像素。')
    if width > 4096 or height > 4096:
        raise boards.BoardError('预览分辨率过大；请明确选择不超过 4096×4096 的预览尺寸。')
    fps = frac(config.get('fps'), 'fps', positive=True)
    if fps > 120:
        raise boards.BoardError('预览帧率超过 120 fps；请核对明确的项目设置。')
    aspect = required_text(config.get('aspect_ratio'), 'aspect_ratio', 30)
    try:
        a, b = aspect.split(':')
        ratio = Fraction(int(a), int(b))
    except (ValueError, ZeroDivisionError):
        raise boards.BoardError('aspect_ratio 必须为明确的宽:高，例如 16:9。') from None
    if ratio <= 0 or ratio != Fraction(width, height):
        raise boards.BoardError('分辨率与明确的画幅比例不一致；请先澄清。')
    source_ref = required_text(config.get('confirmation_ref'), '确认依据', 400)
    policy = config.get('frame_policy')
    if policy not in {'exact', 'nearest_cumulative'}:
        raise boards.BoardError('需明确时长处理方式：exact 严格保持计划秒数，或 nearest_cumulative 在获授权时允许偏差。')
    if policy == 'nearest_cumulative' and (config.get('rounding_authorized') is not True
            or not isinstance(config.get('rounding_ref'), str) or not config['rounding_ref'].strip()):
        raise boards.BoardError('允许时长出现偏差须明确授权并给出依据；不能默默更改计划秒数。')
    if spec and not spec.is_file():
        raise boards.BoardError('指定的 spec 文件不存在。')
    cursor, cumulative = Fraction(0), Fraction(0)
    prev_frame = 0
    entries = []
    for i, (shot, item) in enumerate(zip(shots, raw['shots'])):
        sid = shot['id']
        if any(key not in item for key in ('in', 'out', 'duration', 'timeline_start', 'camera', 'transition_in')):
            raise boards.BoardError(f'{sid} 缺少明确的入出点、时长、时间线位置、运镜或切换方式。')
        if shot['timeline_start'] != cursor:
            raise boards.BoardError(f'{sid} 时间线存在空隙或重叠；本工具只支持相接的硬切。')
        if item['transition_in'] != 'cut':
            raise boards.BoardError(f'{sid} 使用了未支持的转场；目前只支持明确的硬切。')
        shot_size = required_text(item.get('shot_size'), f'{sid} 景别说明', 80)
        camera = item.get('camera')
        if not isinstance(camera, dict) or camera.get('type') not in {'static', 'linear'}:
            raise boards.BoardError(f'{sid} 必须明确 camera.type 为 static 或 linear。')
        start = rect(camera.get('start_crop'), f'{sid} 起始裁切框')
        end = rect(camera.get('end_crop'), f'{sid} 结束裁切框')
        if camera['type'] == 'static' and start != end:
            raise boards.BoardError(f'{sid} 的 static 裁切框前后不一致。')
        path_ = shot['path']
        if path_.suffix.lower() not in boards.IMAGE_EXT or not path_.is_file():
            raise boards.BoardError(f'{sid} 必须指向存在的图片文件；本工具不猜测视频代表帧。')
        try:
            im, transparent = load_image(path_)
            dimensions = list(im.size)
            im.close()
        except (OSError, ValueError) as exc:
            raise boards.BoardError(f'{sid} 图片无法读取：{exc}') from exc
        digest = boards.sha_file(path_)
        if shot['expected_sha256'] and shot['expected_sha256'] != digest:
            raise boards.BoardError(f'{sid} 源图片哈希与声明版本不一致。')
        cumulative += shot['duration']
        requested_boundary = cumulative * fps
        if policy == 'exact' and requested_boundary.denominator != 1:
            nearest = round_half_up(requested_boundary)
            actual = Fraction(nearest - prev_frame, 1) / fps
            error = actual - shot['duration']
            raise boards.BoardError(f"{sid} 计划 {float(shot['duration']):.6f} 秒；当前输出设置最接近约 "
                                    f'{float(actual):.6f} 秒（偏差 {float(error):+.6f} 秒）。'
                                    '请确认是否接受该时长偏差或调整设置；未生成预览。')
        end_frame = int(requested_boundary) if policy == 'exact' else round_half_up(requested_boundary)
        frame_count = end_frame - prev_frame
        if frame_count <= 0:
            raise boards.BoardError(f'{sid} 时长过短，无法生成可播放的独立镜头；请调整预览参数，不静默省镜。')
        if camera['type'] == 'linear' and start != end and frame_count < 2:
            raise boards.BoardError(f'{sid} 时长过短，无法在预览中表现明确的起止裁切框。')
        warnings = []
        if transparent:
            warnings.append('源图有透明区域；预览使用黑色表示透明，不推测背景。')
        for label, box in [('start', start), ('end', end)]:
            crop_width = (box[2] - box[0]) * dimensions[0]
            crop_height = (box[3] - box[1]) * dimensions[1]
            if crop_width / crop_height != Fraction(width, height):
                warnings.append(f'{label} 裁切框比例与画幅不同，将保留内容并补黑边；请核对构图。')
            if crop_width < width or crop_height < height:
                warnings.append(f'{label} 裁切区域小于预览分辨率，缩放后可能不清晰。')
        entries.append({'shot': shot, 'source_sha256': digest, 'source_dimensions': dimensions,
                        'frame_start': prev_frame, 'frame_end_exclusive': end_frame, 'frame_count': frame_count,
                        'camera_type': camera['type'], 'start_crop': start, 'end_crop': end,
                        'shot_size': shot_size, 'transition_in': 'cut', 'framing_warnings': warnings})
        prev_frame = end_frame
        cursor = shot['timeline_start'] + shot['duration']
    return {'path': path, 'manifest_hash': manifest_hash, 'raw': raw, 'spec': spec,
            'spec_sha256': boards.sha_file(spec) if spec else None,
            'width': width, 'height': height, 'fps': fps, 'aspect': aspect,
            'source_ref': source_ref, 'policy': policy, 'planned_duration': cumulative,
            'total_frames': prev_frame, 'entries': entries}


def duration_caption(entry, fps):
    actual = Fraction(entry['frame_count'], 1) / fps
    return f"计划 {float(entry['shot']['duration']):.3f}秒 · 实际 {float(actual):.3f}秒"


def render_frame(entry, n, settings, ft, static_override):
    start, end = entry['start_crop'], entry['end_crop']
    count = entry['frame_count']
    if static_override or entry['camera_type'] == 'static' or count == 1:
        box = start
    else:
        t = Fraction(n, count - 1)
        box = [a + (b - a) * t for a, b in zip(start, end)]
    image = entry['image']
    w, h = image.size
    pixels = [int(round(float(box[0]) * w)), int(round(float(box[1]) * h)),
              int(round(float(box[2]) * w)), int(round(float(box[3]) * h))]
    if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
        raise boards.BoardError(f"{entry['shot']['id']} 裁切框小于一个源像素。")
    crop = image.crop(tuple(pixels))
    scale = min(settings['width'] / crop.width, settings['height'] / crop.height)
    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (settings['width'], settings['height']), 'black')
    canvas.paste(crop, ((settings['width'] - crop.width) // 2, (settings['height'] - crop.height) // 2))
    draw = ImageDraw.Draw(canvas, 'RGBA')
    fps = settings['fps']
    clock = Fraction(entry['frame_start'] + n, 1) / fps
    header = f"布局预演  {entry['shot']['id']}"
    timing = f"当前 {float(clock):.3f}秒 · {entry['shot_size']}"
    duration = duration_caption(entry, fps)
    caption = entry['shot']['planned_action']
    lines = [header, timing, duration]
    if caption:
        lines.append('计划动作（未模拟人物）：' + caption)
    line_height = ft.size + max(2, ft.size // 5)
    limit = settings['width'] - 12
    visible = []
    for line in lines:
        original = line
        while line and draw.textlength(line, font=ft) > limit:
            line = line[:-1]
        if line != original:
            while line and draw.textlength(line + '…', font=ft) > limit:
                line = line[:-1]
            line += '…'
        visible.append(line)
    panel_h = min(settings['height'], 8 + line_height * len(visible))
    draw.rectangle((0, 0, settings['width'], panel_h), fill=(0, 0, 0, 175))
    for row, text in enumerate(visible):
        draw.text((6, 4 + row * line_height), text, font=ft, fill=(255, 255, 255, 255))
    return canvas


def probe(path):
    raw = boards.command(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                          'stream=codec_type,codec_name,nb_read_frames,avg_frame_rate,duration,width,height',
                          '-of', 'json', str(path)])
    try:
        return json.loads(raw.decode('utf-8'))['streams']
    except (ValueError, KeyError, UnicodeError) as exc:
        raise boards.BoardError(f'无法校验预览编码：{exc}') from exc


def create(args):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise boards.BoardError('缺少已安装的 ffmpeg 或 ffprobe；不会自动安装。')
    data = validate(args.manifest)
    output = args.out.resolve()
    if output.exists() or not output.parent.is_dir():
        raise boards.BoardError('输出目录必须不存在，且其父目录必须存在。')
    inputs = [data['path']] + [e['shot']['path'] for e in data['entries']] + ([data['spec']] if data['spec'] else [])
    if any(p == output or p.is_relative_to(output) for p in inputs):
        raise boards.BoardError('输出目录不能包含原素材、清单或规格。')
    font_path = boards.get_font(args.font)
    hashes = {'tool': boards.sha_file(HERE), 'boards_module': boards.sha_file(MODULE), 'font': boards.sha_file(font_path)}
    font_size = max(10, min(24, data['width'] // 45))
    ft = boards.font(font_path, font_size)
    fps = data['fps']
    target = Path(tempfile.mkdtemp(prefix='.kpl-layout-', dir=output.parent))
    process = None
    try:
        video = target / 'layout-preview.mp4'
        command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                   '-s', f"{data['width']}x{data['height']}", '-r', f'{fps.numerator}/{fps.denominator}',
                   '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(video)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for entry in data['entries']:
                entry['image'], _ = load_image(entry['shot']['path'])
                try:
                    for n in range(entry['frame_count']):
                        process.stdin.write(render_frame(entry, n, data, ft, args.static).tobytes())
                finally:
                    entry['image'].close()
                    del entry['image']
            process.stdin.close()
            err = process.stderr.read()
            code = process.wait(timeout=120)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as exc:
            process.kill()
            process.wait()
            raise boards.BoardError(f'预览编码失败：{exc}') from exc
        if code:
            raise boards.BoardError('预览编码失败：' + err.decode('utf-8', 'replace')[-900:])
        streams = probe(video)
        videos = [s for s in streams if s.get('codec_type') == 'video']
        if (len(videos) != 1 or any(s.get('codec_type') == 'audio' for s in streams)
                or int(videos[0].get('nb_read_frames', 0)) != data['total_frames']
                or Fraction(videos[0]['avg_frame_rate']) != fps
                or [videos[0].get('width'), videos[0].get('height')] != [data['width'], data['height']]):
            raise boards.BoardError('编码结果的帧数、帧率、画幅或音轨与清单不符。')
        for entry in data['entries']:
            p = entry['shot']['path']
            if not p.is_file() or boards.sha_file(p) != entry['source_sha256']:
                raise boards.BoardError(f"{entry['shot']['id']} 在渲染期间变化，未交付结果。")
        if boards.sha_file(data['path']) != data['manifest_hash'] or (data['spec'] and boards.sha_file(data['spec']) != data['spec_sha256']):
            raise boards.BoardError('清单或规格在渲染期间变化，未交付结果。')
        if any(boards.sha_file(p) != hashes[k] for k, p in [('tool', HERE), ('boards_module', MODULE), ('font', font_path)]):
            raise boards.BoardError('工具或字体在渲染期间变化，未交付结果。')
        records = []
        for entry in data['entries']:
            shot = entry['shot']
            actual = Fraction(entry['frame_count'], 1) / fps
            boundary = Fraction(entry['frame_end_exclusive'], 1) / fps
            planned_end = shot['timeline_start'] + shot['duration']
            records.append({'shot_id': shot['id'], 'section': shot['section'], 'source_path': str(shot['path']),
                            'source_sha256': entry['source_sha256'], 'source_dimensions': entry['source_dimensions'],
                            'source_version_declared': shot['version'], 'shot_size_declared': entry['shot_size'],
                            'source_window': {'in_seconds': boards.sec(shot['in']), 'out_seconds': boards.sec(shot['out'])},
                            'planned_start_seconds': boards.sec(shot['timeline_start']),
                            'planned_duration_seconds': boards.sec(shot['duration']),
                            'planned_duration_exact': str(shot['duration']),
                            'frame_start': entry['frame_start'], 'frame_end_exclusive': entry['frame_end_exclusive'],
                            'frame_count': entry['frame_count'], 'simulated_duration_seconds': boards.sec(actual),
                            'simulated_duration_exact': str(actual),
                            'duration_error_seconds': boards.sec(actual - shot['duration']),
                            'duration_error_exact': str(actual - shot['duration']),
                            'display': {'time_unit': 'seconds', 'duration_label': duration_caption(entry, fps),
                                        'note': '画面显示值四舍五入到毫秒；精确值保留在索引。'},
                            'boundary_error_seconds': boards.sec(boundary - planned_end),
                            'camera_declared': {'type': entry['camera_type'], 'start_crop': normalized(entry['start_crop']),
                                                'end_crop': normalized(entry['end_crop'])},
                            'camera_applied': 'static_override' if args.static else entry['camera_type'],
                            'framing_warnings': entry['framing_warnings'], 'transition': 'cut', 'planned_action': shot['planned_action'],
                            'performed_character_action': False})
        versions = {exe: boards.command([exe, '-version']).decode('utf-8', 'replace').splitlines()[0]
                    for exe in ('ffmpeg', 'ffprobe')}
        result = {'schema': SCHEMA, 'tool_version': VERSION, 'tool_sha256': hashes['tool'],
                  'boards_module_sha256': hashes['boards_module'], 'created_utc': datetime.now(timezone.utc).isoformat(),
                  'manifest': {'path': str(data['path']), 'sha256': data['manifest_hash']},
                  'spec': {'path': str(data['spec']), 'sha256': data['spec_sha256']} if data['spec'] else None,
                  'settings': {'width': data['width'], 'height': data['height'], 'fps': f'{fps.numerator}/{fps.denominator}',
                               'aspect_ratio': data['aspect'], 'confirmation_ref_declared': data['source_ref'],
                               'frame_policy': data['policy'], 'rounding_ref_declared': data['raw']['layout'].get('rounding_ref'),
                               'static_override': args.static, 'font': str(font_path), 'font_sha256': hashes['font']},
                  'dependencies': {**versions, 'pillow': Image.__version__},
                  'video': {'file': video.name, 'sha256': boards.sha_file(video), 'total_frames': data['total_frames'],
                            'planned_duration_seconds': boards.sec(data['planned_duration']),
                            'planned_duration_exact': str(data['planned_duration']),
                            'encoded_duration_seconds': boards.sec(Fraction(data['total_frames'], 1) / fps),
                            'encoded_duration_exact': str(Fraction(data['total_frames'], 1) / fps),
                            'duration_error_seconds': boards.sec(Fraction(data['total_frames'], 1) / fps - data['planned_duration']),
                            'duration_error_exact': str(Fraction(data['total_frames'], 1) / fps - data['planned_duration']),
                            'audio': 'silent_no_audio_stream'}, 'shots': records, 'review_status': 'unverified',
                  'limitations': ['布局预演只模拟静态图片的二维裁切，不含真实人物动作、表演、景深或视差。',
                                  '声明确认依据不证明用户实际批准；需核验原始来源。',
                                  '计划和实际时长的秒数及偏差已列出；预览编码成功不代表已经播放或完成节奏验收。']}
        (target / 'index.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if output.exists() or target.resolve().parent != output.parent.resolve() or not target.name.startswith('.kpl-layout-'):
            raise boards.BoardError('输出目录在渲染期间出现或临时路径不安全，未覆盖。')
        target.rename(output)
        return {'ok': True, 'output': str(output), 'video': str(output / video.name), 'index': str(output / 'index.json'),
                'timing_seconds': {'planned': result['video']['planned_duration_seconds'],
                                   'actual': result['video']['encoded_duration_seconds'],
                                   'error': result['video']['duration_error_seconds']},
                'framing_warnings': [{'shot_id': r['shot_id'], 'warnings': r['framing_warnings']}
                                     for r in records if r['framing_warnings']],
                'review_status': 'unverified'}
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        if target.exists() and target.resolve().parent == output.parent.resolve() and target.name.startswith('.kpl-layout-'):
            shutil.rmtree(target)


def verify(index_path):
    try:
        index_path = index_path.resolve()
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if index['schema'] != SCHEMA:
            raise ValueError('unsupported schema')
        checks = [('tool', HERE, index['tool_sha256']), ('boards_module', MODULE, index['boards_module_sha256']),
                  ('font', Path(index['settings']['font']), index['settings']['font_sha256'])]
        for key in ('manifest', 'spec'):
            if index.get(key):
                checks.append((key, project_paths.input_file(index_path, index, index[key]['path']), index[key]['sha256']))
        checks.extend(('source:' + s['shot_id'], project_paths.input_file(index_path, index, s['source_path']), s['source_sha256']) for s in index['shots'])
        video = project_paths.output_file(index_path, index, index['video']['file'])
        checks.append(('video', video, index['video']['sha256']))
        issues = []
        if index.get('project_output') is not None:
            issues.extend(project_paths.selection_issues(index_path, index))
            checks.extend([('paths_tool', Path(project_paths.__file__), index['project_output'].get('paths_sha256')),
                           ('publisher', HERE.with_name('project.py'), index['project_output'].get('publisher_sha256'))])
        if index.get('tool_version') != VERSION:
            issues.append({'kind': 'tool_version'})
        for kind, path, expected in checks:
            actual = boards.sha_file(path) if path.is_file() else None
            if actual != expected:
                issues.append({'kind': kind, 'path': str(path), 'expected': expected, 'actual': actual})
        return {'ok': True, 'stale': bool(issues), 'issues': issues, 'review_status': 'unverified',
                'note': '版本一致不证明用户确认、实际播放、节奏或表演已验收。'}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise boards.BoardError(f'无法校验预览索引：{exc}') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('create', help='生成静音的静态图二维布局预演')
    make.add_argument('--manifest', required=True, type=Path, help='明确时间、画幅、运镜及确认依据的清单')
    make.add_argument('--out', required=True, type=Path, help='必须不存在的新输出目录')
    make.add_argument('--font', type=Path, help='已安装中文字体路径')
    make.add_argument('--static', action='store_true', help='关闭全部模拟运镜，保留每镜起始裁切框以对照时长')
    chk = sub.add_parser('verify', help='检查清单、源图、预览、工具和字体是否变更')
    chk.add_argument('index', type=Path)
    args = parser.parse_args(argv)
    try:
        result = create(args) if args.command == 'create' else verify(args.index)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == 'verify' and result['stale'] else 0
    except (boards.BoardError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'review_status': 'unverified'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
