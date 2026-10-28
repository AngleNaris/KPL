#!/usr/bin/env python3
"""Create read-only KPL shot-sequence, motion and cut review boards.

Frames are selected using decoded-frame presentation timestamps. Motion scores
are grayscale pixel differences, not semantic action recognition or verdicts.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
import project_paths

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat
except ImportError:
    print('需要已安装的 Pillow；没有安装依赖。', file=sys.stderr)
    raise SystemExit(2)

VERSION = '1.0.0'
SCHEMA = 'kpl-review-board-v1'
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
BG = '#111820'
PANEL = '#202b36'
WHITE = '#ecf2f5'
MUTED = '#afc2cd'
TEAL = '#70dec9'
GOLD = '#ffce70'
RED = '#ff8e8e'
WIDTH = 1560


class BoardError(Exception):
    pass


def sha_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Fraction)):
        raise BoardError(f'{label} 必须是有限数字')
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        raise BoardError(f'{label} 必须是有限数字') from None
    if result < 0:
        raise BoardError(f'{label} 不能为负数')
    return result


def sec(value):
    return round(float(value), 9)


def seconds_label(value):
    """Human-facing display only; precise values remain in the index."""
    return f'{float(value):.3f}秒'


def stamp(value):
    ms = int(value * 1000 + Fraction(1, 2))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def command(argv):
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
    except FileNotFoundError as exc:
        raise BoardError(f'缺少 {argv[0]}；请使用已安装的工具，不自动安装。') from exc
    except subprocess.TimeoutExpired as exc:
        raise BoardError(f'{argv[0]} 超时；请缩小素材范围后重试。') from exc
    if result.returncode:
        raise BoardError(f'{Path(argv[0]).name} 失败：{result.stderr.decode("utf-8", "replace")[-900:].strip()}')
    return result.stdout


def get_font(path=None):
    win_dir = os.environ.get('WINDIR')
    candidates = ([Path(path)] if path else
                  ([Path(win_dir) / 'Fonts/msyh.ttc'] if win_dir else []) +
                  [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
                   Path('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc'),
                   Path('/System/Library/Fonts/PingFang.ttc')])
    for candidate in candidates:
        if candidate.is_file():
            try:
                ImageFont.truetype(str(candidate), 20)
                return candidate.resolve()
            except OSError:
                continue
    raise BoardError('找不到可用中文字体；请用 --font 指定已安装的中文字体文件。')


def font(path, size):
    return ImageFont.truetype(str(path), size)


def safe_text(value, name, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise BoardError(f'{name} 需要 1–{limit} 个可见字符')
    return value.strip()


def load_manifest(path):
    path = path.resolve()
    try:
        manifest_bytes = path.read_bytes()
        raw = json.loads(manifest_bytes.decode('utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoardError(f'无法读取清单：{exc}') from exc
    if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('shots'), list) or not raw['shots']:
        raise BoardError('清单须为 version:1，且包含非空 shots 数组')
    if len(raw['shots']) > 1000:
        raise BoardError('单次最多 1000 镜；请分批。')
    result, seen = [], set()
    cursor = Fraction(0)
    for index, entry in enumerate(raw['shots']):
        if not isinstance(entry, dict):
            raise BoardError(f'第 {index + 1} 镜不是对象')
        sid = safe_text(entry.get('id'), '镜头 ID', 64)
        if sid in seen:
            raise BoardError(f'镜头 ID 重复：{sid}')
        seen.add(sid)
        media = safe_text(entry.get('media'), f'{sid} 素材路径', 4096)
        path_ = (path.parent / media).resolve()
        duration = number(entry.get('duration'), f'{sid} 入片时长')
        if duration <= 0:
            raise BoardError(f'{sid} 入片时长必须大于 0')
        start = number(entry.get('timeline_start', cursor), f'{sid} 时间线位置')
        if index and start < cursor:
            raise BoardError(f'{sid} 时间线与上一镜重叠；当前工具不处理重叠轨或转场。')
        win_in = number(entry.get('in', 0), f'{sid} 入点')
        win_out = number(entry.get('out', win_in + duration), f'{sid} 出点')
        if win_out <= win_in:
            raise BoardError(f'{sid} 出点必须大于入点')
        if abs((win_out - win_in) - duration) > Fraction(1, 1000000):
            raise BoardError(f'{sid} 采用窗长度与入片时长不一致；当前工具不处理变速。')
        expected = entry.get('sha256')
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64 or
                                     any(c not in '0123456789abcdef' for c in expected)):
            raise BoardError(f'{sid} sha256 须为小写十六进制')
        section = safe_text(entry.get('section', '未分段'), f'{sid} 段落', 100)
        version = entry.get('version')
        if version is not None:
            version = safe_text(version, f'{sid} 版本', 80)
        plan = entry.get('planned_action')
        if plan is not None:
            plan = safe_text(plan, f'{sid} 计划动作', 180)
        result.append({'id': sid, 'path': path_, 'original_path': str(path_), 'input_path': media,
                       'duration': duration, 'timeline_start': start, 'timeline_source': 'explicit' if 'timeline_start' in entry else 'derived',
                       'in': win_in, 'out': win_out, 'section': section, 'version': version,
                       'expected_sha256': expected, 'planned_action': plan})
        cursor = start + duration
    spec = raw.get('spec')
    if spec is not None:
        spec = (path.parent / safe_text(spec, '规格路径', 4096)).resolve()
    return path, hashlib.sha256(manifest_bytes).hexdigest(), raw, result, spec


def probe_video(path):
    output = command(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-show_frames',
        '-show_entries', 'stream=time_base,start_time,duration,width,height,avg_frame_rate:frame=best_effort_timestamp,duration,pkt_duration',
        '-of', 'json', str(path)])
    try:
        obj = json.loads(output)
        stream = obj['streams'][0]
        time_base = Fraction(stream['time_base'])
        if time_base <= 0:
            raise ValueError('invalid time_base')
        rows = obj['frames']
        if not rows:
            raise ValueError('no video frames')
        if len(rows) > 200000:
            raise ValueError('more than 200000 decoded frames; split the source')
        pts = [int(row['best_effort_timestamp']) for row in rows]
        if any(b <= a for a, b in zip(pts, pts[1:])):
            raise ValueError('missing, duplicate or non-monotonic presentation timestamps')
        first = pts[0] * time_base
        rel = [p * time_base - first for p in pts]
        duration_ticks = int(rows[-1].get('duration') or rows[-1].get('pkt_duration') or 0)
        if duration_ticks > 0:
            end = rel[-1] + duration_ticks * time_base
        elif stream.get('duration') not in (None, 'N/A'):
            start = Fraction(str(stream.get('start_time', 0)))
            end = start + Fraction(str(stream['duration'])) - first
        else:
            raise ValueError('cannot determine last frame duration')
        if end <= rel[-1]:
            raise ValueError('invalid last frame duration')
        return {'time_base': time_base, 'first_pts': pts[0], 'pts': pts, 'relative': rel, 'end': end,
                'width': int(stream['width']), 'height': int(stream['height']),
                'avg_frame_rate': stream.get('avg_frame_rate', 'unknown')}
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise BoardError(f'无法建立可靠的解码时间戳：{exc}') from exc


def prepare(shot):
    path = shot['path']
    info = {'id': shot['id'], 'original_path': str(path), 'input_path': shot['input_path'],
            'expected_sha256': shot['expected_sha256'], 'sha256': None, 'status': 'ok', 'error': None,
            'kind': 'image' if path.suffix.lower() in IMAGE_EXT else 'video'}
    if not path.is_file():
        info.update(status='missing', error='素材不存在')
        return info, None
    try:
        info['sha256'] = sha_file(path)
        if info['kind'] == 'image':
            with Image.open(path) as img:
                img.verify()
            info['width'], info['height'] = image_size(path)
            media = None
        else:
            media = probe_video(path)
            if shot['out'] > media['end']:
                raise BoardError(f"采用窗出点 {stamp(shot['out'])} 超出视频末帧覆盖终点 {stamp(media['end'])}")
            frames = within(shot, media)
            if not frames:
                raise BoardError('采用窗内没有可用的解码帧')
            info.update(width=media['width'], height=media['height'], time_base=str(media['time_base']),
                        first_pts=media['first_pts'], frame_count=len(media['pts']),
                        actual_end_seconds=sec(media['end']), avg_frame_rate=media['avg_frame_rate'])
        if shot['expected_sha256'] and shot['expected_sha256'] != info['sha256']:
            info.update(status='version_mismatch', error='素材哈希与清单声明不符；需复核')
        return info, media
    except (OSError, ValueError, BoardError) as exc:
        info.update(status='invalid', error=str(exc))
        return info, None


def image_size(path):
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).size


def within(shot, media):
    # A displayed frame may begin before a non-frame-aligned in-point and remain
    # visible inside the adopted window. Treat each display interval as [PTS, next PTS).
    starts = media['relative']
    return [i for i, start in enumerate(starts)
            if start < shot['out'] and (starts[i + 1] if i + 1 < len(starts) else media['end']) > shot['in']]


def nearest(media, indices, requested):
    values = [media['relative'][i] for i in indices]
    at = bisect.bisect_right(values, requested) - 1
    return indices[max(0, min(at, len(indices) - 1))]


def frame_data(shot, info, media, frame_no=None, requested=None, roles=None, score=None):
    result = {'shot_id': shot['id'], 'source_path': str(shot['path']), 'source_sha256': info['sha256'],
              'kind': info['kind'], 'source_status': info['status'], 'requested_seconds': sec(requested) if requested is not None else None,
              'actual_seconds': None, 'pts': None, 'time_base': None, 'frame_number': None,
              'roles': roles or [], 'motion_score': score, 'source_window': {'in': sec(shot['in']), 'out_exclusive': sec(shot['out'])},
              'timeline_start': sec(shot['timeline_start']), 'duration': sec(shot['duration']),
              'planned_action': shot['planned_action'], 'observed_action': None,
              'version': shot['version'] or (info['sha256'][:12] if info['sha256'] else '未提供')}
    if media is not None and frame_no is not None:
        start = media['relative'][frame_no]
        end = media['relative'][frame_no + 1] if frame_no + 1 < len(media['relative']) else media['end']
        result.update(actual_seconds=sec(media['relative'][frame_no]), pts=media['pts'][frame_no],
                      time_base=str(media['time_base']), frame_number=frame_no,
                      display_interval={'start': sec(start), 'end_exclusive': sec(end)},
                      visible_interval={'start': sec(max(start, shot['in'])), 'end_exclusive': sec(min(end, shot['out']))})
    return result


def pixels(shot, info, frame_no=None):
    if info['status'] in {'missing', 'invalid'}:
        return None
    try:
        if info['kind'] == 'image':
            with Image.open(shot['path']) as img:
                return ImageOps.exif_transpose(img).convert('RGB')
        if frame_no is None:
            return None
        raw = command(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(shot['path']),
                       '-vf', f'select=eq(n\\,{frame_no})', '-fps_mode', 'passthrough',
                       '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'])
        with Image.open(io.BytesIO(raw)) as img:
            return img.convert('RGB')
    except (OSError, ValueError, BoardError) as exc:
        info.update(status='invalid', error=f'帧提取失败：{exc}')
        return None


def motion_scores(shot, media, indices):
    first, last = indices[0], indices[-1]
    size = (96, 54)
    argv = ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(shot['path']), '-vf',
            f'select=between(n\\,{first}\\,{last}),scale=96:54:force_original_aspect_ratio=decrease,'
            'pad=96:54:(ow-iw)/2:(oh-ih)/2:color=black,format=gray',
            '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'gray', '-']
    try:
        with tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=err)
            expired = threading.Event()
            def stop_late():
                if proc.poll() is None:
                    expired.set()
                    proc.kill()
            timer = threading.Timer(120, stop_late)
            timer.daemon = True
            timer.start()
            prev = None
            scores = []
            size_bytes = size[0] * size[1]
            try:
                for idx in range(first, last + 1):
                    block = bytearray()
                    while len(block) < size_bytes:
                        chunk = proc.stdout.read(size_bytes - len(block))
                        if not chunk:
                            break
                        block.extend(chunk)
                    if len(block) != size_bytes:
                        raise BoardError('运动采样超时' if expired.is_set() else '运动采样帧数与 ffprobe 解码帧数不一致')
                    current = Image.frombytes('L', size, bytes(block))
                    if prev is not None:
                        score = ImageStat.Stat(ImageChops.difference(current, prev)).mean[0] / 255.0
                        scores.append((idx, score))
                    prev = current
                proc.stdout.close()
                rc = proc.wait(timeout=120)
                if rc:
                    err.seek(0)
                    raise BoardError('运动抽样失败：' + err.read().decode('utf-8', 'replace')[-500:])
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            return scores
    except FileNotFoundError as exc:
        raise BoardError('缺少 ffmpeg，不能检查视频。') from exc


def wrap(draw, text, ft, width, max_lines=4):
    lines, current = [], ''
    for char in str(text):
        candidate = current + char
        if current and draw.textlength(candidate, font=ft) > width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + '…', font=ft) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += '…'
    return lines


def text_lines(draw, x, y, text, ft, width, color=WHITE, spacing=4, max_lines=3):
    lines = wrap(draw, text, ft, width, max_lines)
    step = ft.size + spacing
    for line in lines:
        draw.text((x, y), line, font=ft, fill=color, stroke_width=0)
        y += step
    return lines, y


def heading_font(draw, text, font_path, width, max_lines):
    for size in (20, 18, 16, 14):
        candidate = font(font_path, size)
        if len(wrap(draw, text, candidate, width, max_lines=1000)) <= max_lines:
            return candidate
    raise BoardError('镜头编号过长，无法完整排入格内；请缩短编号并在规格保留映射。')


def draw_image(canvas, box, image):
    draw = ImageDraw.Draw(canvas)
    x, y, w, h = box
    draw.rounded_rectangle((x, y, x + w, y + h), radius=6, fill='#0b1116')
    if image is None:
        return
    # contain, never crop: preserves the complete subject and aspect ratio.
    fit = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
    canvas.paste(fit, (x + (w - fit.width) // 2, y + (h - fit.height) // 2))


def short_line(value, limit=80):
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def header(draw, font_path, title, subtitle):
    draw.text((48, 30), title, font=font(font_path, 34), fill=WHITE)
    draw.text((48, 79), subtitle, font=font(font_path, 18), fill=MUTED)


def create_canvas(height):
    return Image.new('RGB', (WIDTH, height), BG)


def render_sequence(page_shots, prepared, page_no, font_path, page_path):
    cols, rows = 3, math.ceil(len(page_shots) / 3)
    cell_w, cell_h, gap, left, top = 480, 410, 24, 36, 120
    height = top + rows * (cell_h + 18) + 130
    canvas = create_canvas(height)
    draw = ImageDraw.Draw(canvas)
    section = page_shots[0]['section']
    header(draw, font_path, f'镜头序列  /  {short_line(section, 28)}',
           f'第 {page_no} 页 · 缩略图等格展示；底部时间条按时间线比例 · 观察未验证')
    records = []
    for i, shot in enumerate(page_shots):
        info, media = prepared[shot['id']]
        x, y = left + (i % cols) * (cell_w + gap), top + (i // cols) * (cell_h + 18)
        draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), radius=10, fill=PANEL)
        frame_no, request = None, None
        if media is not None:
            available = within(shot, media)
            request = (shot['in'] + shot['out']) / 2
            frame_no = nearest(media, available, request)
        img = pixels(shot, info, frame_no)
        draw_image(canvas, (x + 12, y + 12, cell_w - 24, 200), img)
        roles = ['representative'] if media else (['still'] if info['kind'] == 'image' and img else ['missing'])
        frame = frame_data(shot, info, media, frame_no, request, roles)
        labels = []
        line = f"{shot['id']}  ·  {seconds_label(shot['duration'])}  ·  时间线 {seconds_label(shot['timeline_start'])}"
        title_font = heading_font(draw, line, font_path, cell_w - 32, 3)
        lines, yy = text_lines(draw, x + 16, y + 218, line, title_font, cell_w - 32, max_lines=3)
        labels.extend(lines)
        version_label = '声明版本' if shot['version'] else 'SHA'
        details = f"{version_label} {frame['version']}  |  " + (f"源相对 {seconds_label(media['relative'][frame_no])}" if media and frame_no is not None else ('静态图' if img else '素材缺失/无效'))
        lines, yy = text_lines(draw, x + 16, yy + 2, details, font(font_path, 16), cell_w - 32, MUTED, max_lines=2)
        labels.extend(lines)
        if shot['planned_action']:
            lines, yy = text_lines(draw, x + 16, yy + 2, '计划：' + shot['planned_action'], font(font_path, 15), cell_w - 32, GOLD, max_lines=2)
            labels.extend(lines)
        if info['status'] != 'ok':
            lines, yy = text_lines(draw, x + 16, yy + 1, '注意：' + str(info['error']), font(font_path, 15), cell_w - 32, RED, max_lines=1)
            labels.extend(lines)
        frame.update(page=page_no, grid_row=i // cols + 1, grid_col=i % cols + 1,
                     image_box=[x + 12, y + 12, cell_w - 24, 200], labels=labels,
                     label_font_size=title_font.size, image_rendered=img is not None)
        records.append(frame)
    ybar = top + rows * (cell_h + 18) + 20
    t0 = page_shots[0]['timeline_start']
    tend = max(s['timeline_start'] + s['duration'] for s in page_shots)
    span = tend - t0
    draw.text((48, ybar - 6), f'时间线  {seconds_label(t0)} → {seconds_label(tend)}', font=font(font_path, 16), fill=MUTED)
    bar_x, bar_y, bar_w, bar_h = 48, ybar + 25, WIDTH - 96, 22
    draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), fill='#344653')
    bars = []
    for i, shot in enumerate(page_shots):
        x0 = bar_x + round(float((shot['timeline_start'] - t0) / span) * bar_w)
        x1 = bar_x + round(float((shot['timeline_start'] + shot['duration'] - t0) / span) * bar_w)
        draw.rectangle((x0, bar_y, max(x0 + 1, x1 - 1), bar_y + bar_h), fill=TEAL if i % 2 == 0 else '#7598b1')
        bars.append({'shot_id': shot['id'], 'x_start': x0, 'x_end': x1, 'timeline_start': sec(shot['timeline_start']), 'duration': sec(shot['duration'])})
    draw.text((48, bar_y + 32), '计划动作不是观察结论；实际运动需原速核看。', font=font(font_path, 16), fill=MUTED)
    canvas.save(page_path, format='PNG')
    return records, bars, [WIDTH, height]


def render_frames(samples, shot, infos, page_no, pages, mode, font_path, page_path, whole_samples=None):
    cols = 4 if mode == 'motion' else 3
    cell_w = (WIDTH - 96 - (cols - 1) * 20) // cols
    image_h = 205 if mode == 'motion' else 245
    cell_h = 375 if mode == 'motion' else 420
    rows = math.ceil(len(samples) / cols)
    top = 125 if mode == 'motion' else 150
    height = top + rows * (cell_h + 18) + (135 if mode == 'motion' else 78)
    canvas = create_canvas(height)
    draw = ImageDraw.Draw(canvas)
    if mode == 'motion':
        header(draw, font_path, f"单镜运动  /  {short_line(shot['id'], 34)}",
               f'第 {page_no}/{pages} 页 · 固定时间基准 + 画面变化候选 · 不是动作识别或通过结论')
    else:
        spacing = (f"请求间隔 {seconds_label(shot['interval'])}；实际间隔见各图"
                   if shot.get('interval') is not None else '实际间隔见各图')
        header(draw, font_path, '切点对照  /  上镜末尾 → 下镜开头',
               f"待核查：{short_line(shot['cut_type'], 24)}  ·  时间线切点 {seconds_label(shot['cut_time'])}  ·  {spacing}")
        draw.text((48, 109), f"上镜 {short_line(shot['left']['id'], 18)} [{seconds_label(shot['left']['in'])}, {seconds_label(shot['left']['out'])})   →   "
                  f"下镜 {short_line(shot['right']['id'], 18)} [{seconds_label(shot['right']['in'])}, {seconds_label(shot['right']['out'])})",
                  font=font(font_path, 16), fill=MUTED)
    records = []
    for i, item in enumerate(samples):
        x, y = 48 + (i % cols) * (cell_w + 20), top + (i // cols) * (cell_h + 18)
        draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), radius=10, fill=PANEL)
        info = infos[item['shot']['id']]
        img = None if item.get('padding') else pixels(item['shot'], info, item['frame_no'])
        draw_image(canvas, (x + 10, y + 10, cell_w - 20, image_h), img)
        frame = frame_data(item['shot'], info, item['media'], item['frame_no'], item.get('requested'), item['roles'], item.get('score'))
        if item.get('padding'):
            detail = '该侧没有更多采用画面'
        elif item['frame_no'] is not None and item['media'] is not None:
            when = seconds_label(item['media']['relative'][item['frame_no']])
            detail = f"源相对 {when}"
        else:
            detail = '静态图' if info['kind'] == 'image' and info['status'] in {'ok', 'version_mismatch'} else '素材缺失/无效'
        label = f"{item['shot']['id']}  ·  {item['role_label']}"
        title_font = heading_font(draw, label, font_path, cell_w - 28, 4)
        lines1, yy = text_lines(draw, x + 14, y + image_h + 18, label, title_font, cell_w - 28, max_lines=4)
        lines2, yy = text_lines(draw, x + 14, yy + 2, detail, font(font_path, 16), cell_w - 28, MUTED, max_lines=1)
        lines3 = []
        if item['frame_no'] is not None and item['media'] is not None and item['media']['relative'][item['frame_no']] < item['shot']['in']:
            lines3, yy = text_lines(draw, x + 14, yy + 2, f"采用窗内从 {seconds_label(item['shot']['in'])} 可见", font(font_path, 15), cell_w - 28, MUTED, max_lines=1)
        if item.get('delta') is not None:
            delta_lines, yy = text_lines(draw, x + 14, yy + 2, f"与上一样本间隔 {seconds_label(item['delta'])}", font(font_path, 15), cell_w - 28, MUTED, max_lines=1)
            lines3.extend(delta_lines)
        lines4 = []
        if info['status'] != 'ok' and not item.get('padding'):
            lines4, yy = text_lines(draw, x + 14, yy + 1, '注意：' + str(info['error']), font(font_path, 14), cell_w - 28, RED, max_lines=1)
        frame.update(page=page_no, grid_row=i // cols + 1, grid_col=i % cols + 1,
                     image_box=[x + 10, y + 10, cell_w - 20, image_h], labels=lines1 + lines2 + lines3 + lines4,
                     label_font_size=title_font.size, image_rendered=img is not None,
                     requested_times=item.get('requests', []),
                     sample_interval_from_previous=sec(item['delta']) if item.get('delta') is not None else None)
        records.append(frame)
    if mode == 'motion':
        ruler_y = top + rows * (cell_h + 18) + 5
        draw.text((48, ruler_y), '时间尺：绿=固定基准，黄=变化候选；按采用窗内可见时间定位', font=font(font_path, 16), fill=MUTED)
        x0, x1, y = 50, WIDTH - 50, ruler_y + 42
        draw.line((x0, y, x1, y), fill=MUTED, width=2)
        window = shot['out'] - shot['in']
        marks = []
        for item in whole_samples or samples:
            if item['frame_no'] is None or item['media'] is None:
                continue
            source_at = item['media']['relative'][item['frame_no']]
            at = max(source_at, shot['in'])
            px = x0 + round(float((at - shot['in']) / window) * (x1 - x0))
            color = GOLD if 'motion_candidate' in item['roles'] else TEAL
            draw.ellipse((px - 5, y - 5, px + 5, y + 5), fill=color)
            marks.append({'frame_number': item['frame_no'], 'actual_seconds': sec(source_at),
                          'visible_start_seconds': sec(at), 'x': px, 'roles': item['roles']})
        draw.text((x0, y + 12), seconds_label(shot['in']), font=font(font_path, 15), fill=MUTED)
        draw.text((x1 - 125, y + 12), seconds_label(shot['out']) + ' 出点', font=font(font_path, 15), fill=MUTED)
    else:
        marks = []
        draw.text((48, height - 55), '以上为实际画面采样；图像差异不证明越轴、接续或速度通过。', font=font(font_path, 16), fill=MUTED)
    canvas.save(page_path, format='PNG')
    return records, marks, [WIDTH, height]


def group_pages(shots, limit):
    pages, current, section = [], [], None
    for shot in shots:
        if current and (shot['section'] != section or len(current) >= limit):
            pages.append(current)
            current = []
        current.append(shot)
        section = shot['section']
    if current:
        pages.append(current)
    return pages


def cut_samples_by_seconds(indices, timestamps, side, count, interval):
    """Sample outward from the cut using actual decoded timestamps, not mean FPS."""
    ordered = list(reversed(indices)) if side == 'left_tail' else list(indices)
    selected = []
    for index in ordered:
        if not selected or abs(timestamps[index] - timestamps[selected[-1]]) >= interval:
            selected.append(index)
            if len(selected) == count:
                break
    return list(reversed(selected)) if side == 'left_tail' else selected


def frame_samples(shot, info, media, interval, max_candidates, threshold, gap):
    if media is None:
        return [{'shot': shot, 'media': None, 'frame_no': None, 'requested': None,
                 'roles': ['still'] if info['kind'] == 'image' and info['status'] != 'invalid' else ['missing'],
                 'role_label': '静态素材' if info['kind'] == 'image' else '缺失'}]
    indices = within(shot, media)
    selected = {}
    def add(idx, role, request=None, score=None):
        if idx not in selected:
            selected[idx] = {'shot': shot, 'media': media, 'frame_no': idx, 'requested': request,
                             'requests': [], 'roles': [], 'score': None}
        if role not in selected[idx]['roles']:
            selected[idx]['roles'].append(role)
        if score is not None:
            selected[idx]['score'] = round(score, 6)
        if request is not None and sec(request) not in selected[idx]['requests']:
            selected[idx]['requests'].append(sec(request))
    add(indices[0], 'first', shot['in'])
    count = math.ceil(float((shot['out'] - shot['in']) / interval))
    if count > 500:
        raise BoardError('固定间隔超过 500 个采样点；请缩小采用窗或增大 --interval。')
    for i in range(count):
        requested = shot['in'] + i * interval
        if requested < shot['out']:
            add(nearest(media, indices, requested), 'fixed_interval', requested)
    add(indices[-1], 'last')
    try:
        scores = motion_scores(shot, media, indices) if max_candidates else []
    except BoardError as exc:
        info.update(status='invalid', error=str(exc))
        return [{'shot': shot, 'media': None, 'frame_no': None, 'requested': None,
                 'roles': ['missing'], 'role_label': '无法解码'}]
    peaks = [(idx, score) for i, (idx, score) in enumerate(scores)
             if score >= threshold and (i == 0 or score >= scores[i - 1][1]) and
                (i == len(scores) - 1 or score >= scores[i + 1][1])]
    chosen = []
    for idx, score in sorted(peaks, key=lambda p: (-p[1], p[0])) if max_candidates else []:
        if all(abs(media['relative'][idx] - media['relative'][j]) >= gap for j in chosen):
            chosen.append(idx)
            add(idx, 'motion_candidate', media['relative'][idx], score)
            if len(chosen) >= max_candidates:
                break
    result = [selected[i] for i in sorted(selected)]
    for i, item in enumerate(result):
        labels = {'first': '起始画面', 'fixed_interval': '固定基准', 'last': '结束画面', 'motion_candidate': '变化候选'}
        item['role_label'] = ' / '.join(labels[x] for x in item['roles'])
        if i:
            item['delta'] = media['relative'][item['frame_no']] - media['relative'][result[i - 1]['frame_no']]
    return result


def create(args):
    manifest_path, manifest_hash, raw, shots, spec = load_manifest(args.manifest)
    output = args.out.resolve()
    if output.exists():
        raise BoardError('输出目录已存在；为保护已有文件请指定新的目录。')
    if not output.parent.is_dir():
        raise BoardError('输出目录的父目录不存在。')
    inputs = [manifest_path] + [s['path'] for s in shots] + ([spec] if spec else [])
    if any(p == output or p.is_relative_to(output) for p in inputs):
        raise BoardError('输出目录不能包含输入文件或素材。')
    font_path = get_font(args.font)
    font_hash = sha_file(font_path)
    tool_hash = sha_file(Path(__file__).resolve())
    if spec is not None and not spec.is_file():
        raise BoardError('清单指定的 spec 文件不存在。')
    if args.mode in {'motion', 'cut'}:
        ids = [args.shot] if args.mode == 'motion' else [args.left, args.right]
        lookup = {s['id']: s for s in shots}
        if any(i not in lookup for i in ids):
            raise BoardError('指定的镜头 ID 不在清单中。')
        selected = [lookup[i] for i in ids]
        if args.mode == 'cut' and (args.left == args.right or shots.index(selected[1]) != shots.index(selected[0]) + 1):
            raise BoardError('切点必须选择清单顺序相邻的两个不同镜头。')
        if args.mode == 'cut' and selected[1]['timeline_start'] != selected[0]['timeline_start'] + selected[0]['duration']:
            raise BoardError('两个镜头的时间线不相接；有空隙时请明确空镜或重新核对编排。')
    else:
        selected = shots
    prepared = {s['id']: prepare(s) for s in selected}
    spec_hash = sha_file(spec) if spec else None
    target = Path(tempfile.mkdtemp(prefix='.kpl-review-', dir=output.parent))
    try:
        pages, cells = [], []
        if args.mode == 'sequence':
            for pno, group in enumerate(group_pages(shots, args.page_size), 1):
                name = f'sequence-{pno:03d}.png'
                records, bars, dimensions = render_sequence(group, prepared, pno, font_path, target / name)
                cells.extend(records)
                pages.append({'page': pno, 'file': name, 'dimensions': dimensions, 'timeline_bars': bars})
        elif args.mode == 'motion':
            shot = selected[0]
            info, media = prepared[shot['id']]
            samples = frame_samples(shot, info, media, number(args.interval, '采样间隔'), args.candidates, args.threshold, number(args.gap, '候选间距'))
            batches = [samples[i:i + args.page_size] for i in range(0, len(samples), args.page_size)]
            for pno, batch in enumerate(batches, 1):
                name = f'motion-{pno:03d}.png'
                records, marks, dimensions = render_frames(batch, shot, {shot['id']: info}, pno, len(batches), 'motion', font_path, target / name, samples)
                cells.extend(records)
                pages.append({'page': pno, 'file': name, 'dimensions': dimensions, 'time_ruler': marks})
        else:
            samples = []
            for side, shot in zip(('left_tail', 'right_head'), selected):
                info, media = prepared[shot['id']]
                indices = within(shot, media) if media else []
                chosen = (cut_samples_by_seconds(indices, media['relative'], side, args.frames, number(args.interval, '采样间隔'))
                          if media and args.interval is not None else
                          (list(reversed(indices[::-args.stride][:args.frames])) if side == 'left_tail' else indices[::args.stride][:args.frames]))
                if not chosen:
                    chosen = [None]
                for idx, frame_no in enumerate(chosen):
                    item = {'shot': shot, 'media': media, 'frame_no': frame_no, 'roles': [side],
                            'role_label': '上镜末尾' if side == 'left_tail' else '下镜开头'}
                    if idx and frame_no is not None and media:
                        item['delta'] = media['relative'][frame_no] - media['relative'][chosen[idx - 1]]
                    samples.append(item)
            # Keep the two sides on separate rows even when a clip has fewer frames.
            left_count = len([s for s in samples if s['roles'] == ['left_tail']])
            if left_count < 3:
                samples[left_count:left_count] = [{'shot': selected[0], 'media': None, 'frame_no': None,
                    'roles': ['padding'], 'role_label': '无更多采用画面', 'padding': True}] * (3 - left_count)
            name = 'cut-001.png'
            marker = {'cut_type': args.cut_type, 'left': selected[0], 'right': selected[1],
                      'cut_time': selected[0]['timeline_start'] + selected[0]['duration'],
                      'interval': number(args.interval, '采样间隔') if args.interval is not None else None}
            records, marks, dimensions = render_frames(samples, marker, {s['id']: prepared[s['id']][0] for s in selected},
                                                       1, 1, 'cut', font_path, target / name)
            # Padding is not a decoded sample and must not masquerade as an image frame.
            for rec, item in zip(records, samples):
                if item.get('padding'):
                    rec['kind'], rec['roles'] = 'padding', ['padding']
            cells.extend(records)
            pages.append({'page': 1, 'file': name, 'dimensions': dimensions})
        for page in pages:
            page['sha256'] = sha_file(target / page['file'])
        for shot in selected:
            info = prepared[shot['id']][0]
            if info['sha256'] and (not shot['path'].is_file() or sha_file(shot['path']) != info['sha256']):
                raise BoardError(f"{shot['id']} 在生成期间发生变化；未写入结果。")
            if info['status'] == 'missing' and shot['path'].exists():
                raise BoardError(f"{shot['id']} 在生成期间出现；未写入结果。")
        if sha_file(manifest_path) != manifest_hash or (spec and sha_file(spec) != spec_hash):
            raise BoardError('清单或规格在生成期间发生变化；未写入结果。')
        if sha_file(font_path) != font_hash:
            raise BoardError('字体文件在生成期间发生变化；未写入结果。')
        if sha_file(Path(__file__).resolve()) != tool_hash:
            raise BoardError('工具文件在生成期间发生变化；未写入结果。')
        params = {'mode': args.mode, 'font': str(font_path)}
        for name in ('page_size', 'interval', 'candidates', 'threshold', 'gap', 'frames', 'stride', 'cut_type', 'shot', 'left', 'right'):
            if hasattr(args, name):
                params[name] = getattr(args, name)
        versions = {}
        for executable in ('ffmpeg', 'ffprobe'):
            versions[executable] = command([executable, '-version']).decode('utf-8', 'replace').splitlines()[0] if shutil.which(executable) else None
        report = {'schema': SCHEMA, 'tool_version': VERSION, 'tool_sha256': tool_hash,
                  'created_utc': datetime.now(timezone.utc).isoformat(),
                  'mode': args.mode, 'parameters': params, 'manifest': {'path': str(manifest_path), 'sha256': manifest_hash},
                  'spec': {'path': str(spec), 'sha256': spec_hash} if spec else None,
                  'dependencies': {**versions, 'pillow': Image.__version__, 'font_sha256': font_hash},
                  'sources': [prepared[s['id']][0] for s in selected], 'pages': pages, 'cells': cells,
                  'review_status': 'unverified',
                  'limitations': ['拼图与画面差异分数不是语义动作识别或验收结论。', '闪光、镜头运动与场景变化可能产生高分；固定基准帧保留停滞线索。',
                                  '需回看原素材与相邻剪辑的正常播放速度；有音乐时结合原曲。', '帧号为解码显示顺序，从 0 开始；源时间相对首个解码帧，PTS 与 time_base 保留原值。',
                                  '非帧边界按 [PTS, 下一帧PTS) 的显示区间取与采用窗有交集的帧；实际剪辑软件若采用其他取帧策略须另行核对。']}
        if args.mode == 'cut':
            report['cut'] = {'left': selected[0]['id'], 'right': selected[1]['id'],
                             'timeline_seconds': sec(selected[0]['timeline_start'] + selected[0]['duration']),
                             'declared_type': args.cut_type, 'frame_stride': args.stride if args.interval is None else None,
                             'requested_interval_seconds': args.interval, 'verdict': 'unverified'}
        (target / 'index.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if output.exists():
            raise BoardError('输出目录在生成期间出现；未覆盖。')
        if target.resolve().parent != output.parent.resolve() or not target.name.startswith('.kpl-review-'):
            raise BoardError('临时目录不在预期输出目录内。')
        target.rename(output)
        return {'ok': True, 'output': str(output), 'index': str(output / 'index.json'), 'pages': len(pages),
                'status': 'partial' if any(x['status'] != 'ok' for x in report['sources']) else 'ready_for_review',
                'review_status': 'unverified', 'sources_with_issues': [x['id'] for x in report['sources'] if x['status'] != 'ok']}
    finally:
        if target.exists():
            if target.resolve().parent == output.parent.resolve() and target.name.startswith('.kpl-review-'):
                shutil.rmtree(target)


def verify(path):
    try:
        index = json.loads(path.read_text(encoding='utf-8'))
        if index.get('schema') != SCHEMA:
            raise ValueError('unsupported schema')
        issues = []
        if index.get('project_output') is not None:
            issues.extend(project_paths.selection_issues(path, index))
            for key, tool in [('paths_sha256', Path(project_paths.__file__)), ('publisher_sha256', Path(__file__).with_name('project.py'))]:
                actual = sha_file(tool) if tool.is_file() else None
                if index['project_output'].get(key) != actual:
                    issues.append({'kind': key, 'expected': index['project_output'].get(key), 'actual': actual})
        if index.get('tool_version') != VERSION:
            issues.append({'kind': 'tool_version', 'expected': index.get('tool_version'), 'actual': VERSION})
        current_tool = sha_file(Path(__file__).resolve())
        if index.get('tool_sha256') != current_tool:
            issues.append({'kind': 'tool_code', 'expected': index.get('tool_sha256'), 'actual': current_tool})
        font_path = Path(index.get('parameters', {}).get('font', ''))
        font_hash = sha_file(font_path) if font_path.is_file() else None
        if font_hash != index.get('dependencies', {}).get('font_sha256'):
            issues.append({'kind': 'font', 'path': str(font_path), 'expected': index.get('dependencies', {}).get('font_sha256'), 'actual': font_hash})
        for category in ('manifest', 'spec'):
            item = index.get(category)
            if not item:
                continue
            source = project_paths.input_file(path, index, item['path'])
            actual = sha_file(source) if source.is_file() else None
            if actual != item.get('sha256'):
                issues.append({'kind': category, 'path': str(source), 'expected': item.get('sha256'), 'actual': actual})
        for source in index['sources']:
            path_ = project_paths.input_file(path, index, source['original_path'])
            actual = sha_file(path_) if path_.is_file() else None
            if actual != source.get('sha256') or source.get('status') != 'ok':
                issues.append({'kind': 'source', 'shot_id': source['id'], 'path': str(path_),
                               'expected': source.get('sha256'), 'actual': actual, 'original_status': source.get('status')})
        for page in index['pages']:
            p = project_paths.output_file(path, index, page['file'])
            actual = sha_file(p) if p.is_file() else None
            if actual != page.get('sha256'):
                issues.append({'kind': 'page', 'path': str(p), 'expected': page.get('sha256'), 'actual': actual})
        return {'ok': True, 'stale': bool(issues), 'issues': issues, 'review_status': 'unverified',
                'note': '哈希核对不验证语义、运动、观看记录或审美；索引本身应在规格中登记哈希。'}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise BoardError(f'无法验证索引：{exc}') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    seq = sub.add_parser('sequence', help='按清单顺序绘制分段镜头序列总览')
    mot = sub.add_parser('motion', help='单镜固定间隔加画面差异候选；不做语义识别')
    cut = sub.add_parser('cut', help='相邻镜头采用窗尾部与开头对照')
    for p in (seq, mot, cut):
        p.add_argument('--manifest', type=Path, required=True, help='UTF-8 JSON 镜头清单')
        p.add_argument('--out', type=Path, required=True, help='必须尚不存在的输出目录')
        p.add_argument('--font', help='中文字体文件；省略使用本机已安装字体')
    seq.add_argument('--page-size', type=int, default=9, choices=range(1, 10), help='每页最多镜头数，默认 9')
    mot.add_argument('--shot', required=True, help='镜头 ID')
    mot.add_argument('--interval', type=float, default=.5, help='固定基准间隔（秒），默认 0.5')
    mot.add_argument('--candidates', type=int, choices=range(0, 7), default=3, help='额外变化候选数，默认 3')
    mot.add_argument('--threshold', type=float, default=.01, help='灰度差分候选阈值，默认 0.01')
    mot.add_argument('--gap', type=float, default=.3, help='候选最小时间间距，默认 0.3 秒')
    mot.add_argument('--page-size', type=int, choices=range(1, 13), default=12, help='每页最多帧数，默认 12')
    cut.add_argument('--left', required=True, help='上镜 ID')
    cut.add_argument('--right', required=True, help='下镜 ID，须与上镜相邻')
    cut.add_argument('--samples', '--frames', dest='frames', type=int, choices=range(1, 4), default=3, help='每侧最多采样画面数；--frames 是旧版别名')
    spacing = cut.add_mutually_exclusive_group()
    spacing.add_argument('--interval', type=float, help='采样时间间隔（秒），依据实际时间戳选取')
    spacing.add_argument('--stride', type=int, choices=range(1, 101), default=1, help='旧版解码帧步长；新审查请使用 --interval 秒')
    cut.add_argument('--cut-type', default='未指定', help='人工声明的待核查接续类型；不自动判定')
    check = sub.add_parser('verify', help='核对索引所引用的清单、规格、素材与页面哈希')
    check.add_argument('index', type=Path, help='生成的 index.json')
    args = parser.parse_args(argv)
    try:
        if args.mode == 'motion' and (not math.isfinite(args.interval) or args.interval <= 0 or
                not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1 or not math.isfinite(args.gap) or args.gap < 0):
            raise BoardError('interval 必须大于 0；threshold 在 0–1，gap 不小于 0。')
        if args.mode == 'cut':
            if args.interval is not None and (not math.isfinite(args.interval) or args.interval <= 0):
                raise BoardError('采样间隔必须大于 0 秒。')
            args.cut_type = safe_text(args.cut_type, '接续类型', 100)
        result = verify(args.index.resolve()) if args.mode == 'verify' else create(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.mode == 'verify' and result['stale'] else 0
    except (BoardError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
