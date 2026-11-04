"""Create and parse a self-contained, user-owned KPL HTML specification."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager

SCHEMA = 'kpl-user-spec-v2'
# v3 只比 v2 多一个 timeline（媒体轨与标记）；原生编辑器加入媒体后保存时原地升级，HTML 规格仍是 v2
SCHEMA_V3 = 'kpl-user-spec-v3'
ROOT_FIELDS = ('schema', 'document_id', 'version', 'status', 'confirmation', 'revision',
               'title', 'summary', 'global_settings', 'layout_aspect', 'assets', 'shots')
# 音频轨只能关声音；视频轨可分别关声音（muted）与画面（hidden）；字幕轨只能关画面
# locked：用户锁定的行，编辑器不让改、Agent 也不改其中内容
TRACK_FIELDS = {'id', 'name', 'muted', 'locked', 'gain', 'clips'}
VIDEO_TRACK_FIELDS = TRACK_FIELDS | {'hidden'}
# 主轨片段由所属镜头的格子定位，没有 start_us；自由视频轨与音轨的片段自带时间位置
# takes：同一镜头的其他候选版本（多次生成的结果），顶层字段是当前使用的版本；切换就是两者互换
MAIN_CLIP_FIELDS = {'id', 'shot_id', 'source', 'in_us', 'out_us', 'audio', 'takes'}
TAKE_FIELDS = {'id', 'source', 'in_us', 'out_us', 'audio'}
VIDEO_CLIP_FIELDS = {'id', 'start_us', 'source', 'in_us', 'out_us', 'audio'}
AUDIO_CLIP_FIELDS = {'id', 'start_us', 'source', 'in_us', 'out_us'}
MARKER_FIELDS = {'id', 'start_us', 'end_us', 'text', 'author'}
SUBTITLE_TRACK_FIELDS = {'id', 'name', 'language', 'hidden', 'locked', 'cues'}
CUE_FIELDS = {'id', 'start_us', 'end_us', 'text', 'words'}
WORD_FIELDS = {'start_us', 'end_us', 'text'}
CUE_TEXT_LIMIT = 2000
MAX_US = 24 * 3600 * 1_000_000
TIMELINE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
# 文本字段；layout_camera 是预览运镜对象或 null，单独校验
FIELDS = ('number', 'duration_seconds', 'thumbnail', 'content', 'description', 'shot_scale', 'camera_motion', 'prompt',
          'video_prompt', 'mark')
SHOT_FIELDS = ('id',) + FIELDS + ('layout_camera',)
ASSET_FIELDS = ('id', 'name', 'path')
MARKS = {'': '未标记', 'done': '完成', 'revise': '待修改', 'wip': '进行中', 'question': '有疑问', 'hold': '搁置'}
ASSET_DIR = '../05_参考资产/'
# 描述与提示词中的分段标记 {类别:内容}；只读视图只显示内容（格式见 references/spec-format.md）
DESCRIPTION_LIMIT = 300
SEGMENTED_FIELDS = ('description', 'prompt', 'video_prompt')
SEGMENT = re.compile(r'\{(?:镜头|风格|场景|动态|约束)[:：]((?:@\{[^{}]*\}|[^{}])*)\}')
TAGGED_SEGMENT = re.compile(r'\{(镜头|风格|场景|动态|约束)[:：]((?:@\{[^{}]*\}|[^{}])*)\}')
# 视频提示词的动态时段只用固定写法 {动态:0-4秒：内容}，精确到 0.1 秒；其他写法的时间范围一律拒绝
BEAT = re.compile(r'((?:0|[1-9]\d*)(?:\.\d)?)-((?:0|[1-9]\d*)(?:\.\d)?)秒：')
LOOSE_TIME = re.compile(r'\d+(?:\.\d+)?\s*秒?\s*(?:到|至|~|～|-|－|—|–)\s*\d+(?:\.\d+)?\s*秒')
HERE = Path(__file__).resolve().parent.parent
APP = HERE / 'assets' / 'user-spec.js'


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON key: ' + key)
        out[key] = value
    return out


def safe_thumbnail(value):
    """Images may only come from the project's reference-asset folder."""
    if not isinstance(value, str) or not value.startswith(ASSET_DIR) or re.search(r'[\\:%?#\x00-\x1f]', value):
        return False
    parts = value.split('/')[2:]
    return bool(parts and all(part not in ('', '.', '..') for part in parts)
                and re.search(r'\.(?:png|jpe?g|webp|gif)$', value, re.I))


def valid_camera(value):
    """预览运镜与 layout 预演清单的 camera 相同：null，或 static/linear 加两个 [左, 上, 右, 下] 归一化裁切框。"""
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {'type', 'start_crop', 'end_crop'} or value['type'] not in ('static', 'linear'):
        return False
    for box in (value['start_crop'], value['end_crop']):
        if (not isinstance(box, list) or len(box) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in box) or
                box[0] >= box[2] or box[1] >= box[3]):
            return False
    return value['type'] == 'linear' or value['start_crop'] == value['end_crop']


def valid_aspect(value):
    """预览画幅：空（按 16:9 预览）或“宽:高”，两边都是正数。"""
    match = re.fullmatch(r'(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)', value)
    return value == '' or bool(match and float(match[1]) > 0 and float(match[2]) > 0)


def valid_source(value):
    """媒体路径相对 .kpldoc 所在目录（素材在其他盘时可为绝对路径），统一用 / 分隔。"""
    return isinstance(value, str) and value.strip() == value and value != '' and not re.search(r'[\\\x00-\x1f]', value)


def _us(value):
    return type(value) is int and 0 <= value <= MAX_US


def validate_timeline(timeline, shot_ids):
    """时间单位为整数微秒，片段采用素材的 [in_us, out_us)。video[0] 是主轨，序号越大越靠上并覆盖下层；
    字幕轨独立于音视频轨，条目是不依赖素材的文本，words 为零个或多个逐段时间。"""
    if (not isinstance(timeline, dict) or set(timeline) != {'video', 'audio', 'subtitles', 'markers', 'markers_locked'}
            or type(timeline['markers_locked']) is not bool):
        raise ValueError('invalid timeline structure')
    video, audio, markers = timeline['video'], timeline['audio'], timeline['markers']
    subtitles = timeline.get('subtitles')
    if (not isinstance(video, list) or not video or not isinstance(audio, list) or not isinstance(markers, list)
            or not isinstance(subtitles, list)):
        raise ValueError('timeline needs a main video track and track/marker lists')
    ids, linked = set(), set()

    def new_id(value):
        if not isinstance(value, str) or not TIMELINE_ID.fullmatch(value) or value in ids:
            raise ValueError('invalid or duplicate timeline ID: %r' % (value,))
        ids.add(value)

    for kind, tracks in (('video', video), ('audio', audio)):
        for index, track in enumerate(tracks):
            main = kind == 'video' and index == 0
            if (not isinstance(track, dict) or set(track) != (VIDEO_TRACK_FIELDS if kind == 'video' else TRACK_FIELDS)
                    or not isinstance(track['name'], str) or type(track['muted']) is not bool
                    or kind == 'video' and type(track['hidden']) is not bool or type(track['locked']) is not bool
                    or isinstance(track['gain'], bool)
                    or not isinstance(track['gain'], (int, float)) or not 0 <= track['gain'] <= 1
                    or not isinstance(track['clips'], list)):
                raise ValueError('invalid %s track' % kind)
            new_id(track['id'])
            fields = MAIN_CLIP_FIELDS if main else VIDEO_CLIP_FIELDS if kind == 'video' else AUDIO_CLIP_FIELDS
            spans = []
            for clip in track['clips']:
                if not isinstance(clip, dict) or set(clip) != fields or not valid_source(clip['source']):
                    raise ValueError('invalid clip in track ' + str(track.get('id')))
                new_id(clip['id'])
                if not _us(clip['in_us']) or not _us(clip['out_us']) or clip['out_us'] <= clip['in_us']:
                    raise ValueError('clip %s needs 0 <= in_us < out_us' % clip['id'])
                if 'audio' in fields and type(clip['audio']) is not bool:
                    raise ValueError('clip audio flag must be boolean')
                if main:
                    # 每个镜头最多关联一个视频
                    if clip['shot_id'] not in shot_ids or clip['shot_id'] in linked:
                        raise ValueError('main-track clip must link one existing shot once: ' + str(clip['shot_id']))
                    linked.add(clip['shot_id'])
                    if not isinstance(clip['takes'], list):
                        raise ValueError('clip takes must be a list')
                    for take in clip['takes']:
                        if (not isinstance(take, dict) or set(take) != TAKE_FIELDS or not valid_source(take['source'])
                                or not _us(take['in_us']) or not _us(take['out_us']) or take['out_us'] <= take['in_us']
                                or type(take['audio']) is not bool):
                            raise ValueError('invalid take in clip ' + str(clip['id']))
                        new_id(take['id'])
                else:
                    end = clip['start_us'] + clip['out_us'] - clip['in_us'] if _us(clip['start_us']) else -1
                    if not 0 <= end <= MAX_US:
                        raise ValueError('clip %s is outside the timeline range' % clip['id'])
                    spans.append((clip['start_us'], end))
            spans.sort()
            if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
                raise ValueError('clips on track %s overlap' % track['id'])
    for track in subtitles:
        if (not isinstance(track, dict) or set(track) != SUBTITLE_TRACK_FIELDS or not isinstance(track['name'], str)
                or not isinstance(track['language'], str) or type(track['hidden']) is not bool or type(track['locked']) is not bool
                or not isinstance(track['cues'], list)):
            raise ValueError('invalid subtitle track')
        new_id(track['id'])
        spans = []
        for cue in track['cues']:
            if (not isinstance(cue, dict) or set(cue) != CUE_FIELDS or not _us(cue['start_us'])
                    or not _us(cue['end_us']) or cue['end_us'] <= cue['start_us']
                    or not isinstance(cue['text'], str) or len(cue['text']) > CUE_TEXT_LIMIT or not isinstance(cue['words'], list)):
                raise ValueError('invalid subtitle cue')
            new_id(cue['id'])
            last = cue['start_us']
            for word in cue['words']:
                # 逐字时间来自 ASS 的卡拉 OK 标签：按顺序、落在本句范围内、互不重叠
                if (not isinstance(word, dict) or set(word) != WORD_FIELDS or type(word['start_us']) is not int
                        or type(word['end_us']) is not int or word['end_us'] <= word['start_us']
                        or word['start_us'] < last or word['end_us'] > cue['end_us'] or not isinstance(word['text'], str)
                        or not word['text']):
                    raise ValueError('invalid subtitle word timing in cue ' + str(cue['id']))
                last = word['end_us']
            spans.append((cue['start_us'], cue['end_us']))
        spans.sort()
        if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            raise ValueError('subtitle cues on track %s overlap' % track['id'])
    for marker in markers:
        if (not isinstance(marker, dict) or set(marker) != MARKER_FIELDS or not _us(marker['start_us'])
                or not (marker['end_us'] is None or _us(marker['end_us']) and marker['end_us'] > marker['start_us'])
                or not isinstance(marker['text'], str) or marker['author'] not in ('user', 'agent')):
            raise ValueError('invalid timeline marker')
        new_id(marker['id'])
    return timeline


def validate(model):
    """Strict v2/v3 contract: exact fields, unique stable IDs, safe asset paths and known marks."""
    v3 = isinstance(model, dict) and model.get('schema') == SCHEMA_V3
    if (not isinstance(model, dict) or set(model) != set(ROOT_FIELDS + (('timeline',) if v3 else ())) or
            model.get('schema') not in (SCHEMA, SCHEMA_V3) or
            not isinstance(model.get('document_id'), str) or not model['document_id'].strip() or
            not isinstance(model.get('version'), str) or
            not re.fullmatch(r'v(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})', model['version']) or
            model.get('status') not in ('draft', 'review', 'final') or
            isinstance(model.get('revision'), bool) or not isinstance(model.get('revision'), int) or
            not 1 <= model['revision'] < 2**31 - 1 or
            model.get('confirmation') not in ('unconfirmed', 'confirmed') or
            any(not isinstance(model.get(k), str) for k in ('title', 'summary', 'global_settings', 'layout_aspect')) or
            not valid_aspect(model['layout_aspect']) or
            not isinstance(model.get('assets'), list) or not isinstance(model.get('shots'), list)):
        raise ValueError('invalid HTML specification structure')
    asset_ids = set()
    for asset in model['assets']:
        if (not isinstance(asset, dict) or set(asset) != set(ASSET_FIELDS) or
                any(not isinstance(asset[k], str) for k in ASSET_FIELDS) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', asset['id']) or asset['id'] in asset_ids or
                not asset['name'].strip() or not safe_thumbnail(asset['path'])):
            raise ValueError('invalid or duplicate asset')
        asset_ids.add(asset['id'])
    ids, numbers = set(), set()
    for row in model['shots']:
        if (not isinstance(row, dict) or set(row) != set(SHOT_FIELDS) or
                not isinstance(row.get('id'), str) or not row['id'] or row['id'] in ids or
                any(not isinstance(row[k], str) for k in FIELDS)):
            raise ValueError('invalid or duplicate stable shot ID')
        ids.add(row['id'])
        number = row['number'].strip().casefold()
        if not number or number in numbers:
            raise ValueError('missing or duplicate shot number')
        numbers.add(number)
        duration = row['duration_seconds'].strip()
        if duration:
            # 时长只精确到 0.1 秒；从帧数或音乐换算时按累计切点取 0.1 秒整，不要逐镜四舍五入以免总长漂移
            if not re.fullmatch(r'(?:\d+(?:\.\d)?|\.\d)', duration):
                raise ValueError('duration must be positive seconds with at most one decimal place (0.1 s), '
                                 'e.g. 1.4; round cumulative cut points to 0.1 s instead of each shot: ' + duration)
            try:
                value = Decimal(duration)
                if not value.is_finite() or value <= 0:
                    raise ValueError('duration must be positive')
            except InvalidOperation as exc:
                raise ValueError('invalid duration') from exc
            if len(value.as_tuple().digits) > 28 or value.as_tuple().exponent < -28:
                raise ValueError('duration is outside the supported range')
        # 详细描述只概括画面，按显示字数（不计分段标记）限 300 字；来源、运镜原文、工作流等记录放项目状态文件
        if len(plain_text(row['description'])) > DESCRIPTION_LIMIT:
            raise ValueError('description of %s has %d visible characters; limit is %d. Describe only what the picture '
                             'shows; keep sources, camera notes, workflow paths and review records in project state files, '
                             'not in the spec' % (row['number'], len(plain_text(row['description'])), DESCRIPTION_LIMIT))
        if row['thumbnail'] and not safe_thumbnail(row['thumbnail']):
            raise ValueError('thumbnail must be inside project reference assets')
        if row['mark'] not in MARKS:
            raise ValueError('unknown shot mark')
        if not valid_camera(row['layout_camera']):
            raise ValueError('layout_camera must be null or a static/linear crop camera')
    if v3:
        validate_timeline(model['timeline'], ids)
    return model


def validate_legacy(model):
    """Validate the original v1 structure for reading only; never map or rewrite it."""
    root = {'schema', 'document_id', 'version', 'status', 'confirmation', 'revision',
            'title', 'summary', 'global_settings', 'shots'}
    fields = {'id', 'number', 'name', 'duration_seconds', 'thumbnail', 'action',
              'prompt', 'references', 'settings', 'acceptance'}
    if (not isinstance(model, dict) or set(model) != root or model.get('schema') != 'kpl-user-spec-v1'
            or any(not isinstance(model.get(k), str) for k in root - {'revision', 'shots'})
            or not model['document_id'].strip() or not re.fullmatch(r'v(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})', model['version'])
            or model['status'] not in ('draft', 'review', 'final')
            or model['confirmation'] not in ('unconfirmed', 'confirmed')
            or isinstance(model['revision'], bool) or not isinstance(model['revision'], int)
            or not 1 <= model['revision'] < 2**31 - 1 or not isinstance(model['shots'], list)):
        raise ValueError('invalid legacy v1 specification')
    ids, numbers = set(), set()
    for row in model['shots']:
        if (not isinstance(row, dict) or set(row) != fields or any(not isinstance(row[k], str) for k in fields)
                or not row['id'] or row['id'] in ids or not row['number'].strip()
                or row['number'].strip().casefold() in numbers):
            raise ValueError('invalid legacy v1 shot')
        ids.add(row['id']); numbers.add(row['number'].strip().casefold())
    return model


def new_shot(ident, number):
    return {'id': ident, **{field: '' for field in FIELDS}, 'number': number, 'layout_camera': None}


def new_model(title):
    return {'schema': SCHEMA, 'document_id': str(uuid.uuid4()), 'version': 'v001', 'status': 'draft',
            'confirmation': 'unconfirmed', 'revision': 1, 'title': title, 'summary': '',
            'global_settings': '', 'layout_aspect': '', 'assets': [], 'shots': []}


def json_for_script(model):
    raw = json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False)
    return (raw.replace('&', r'\u0026').replace('<', r'\u003c').replace('>', r'\u003e')
            .replace('\u2028', r'\u2028').replace('\u2029', r'\u2029'))


def plain_text(value):
    return SEGMENT.sub(r'\1', value)


def check_segments(label, value):
    """Agent 写入的描述与提示词：每个非空行恰好是一个完整分段，行外不留未分类的文字。"""
    for number, line in enumerate(value.split('\n'), 1):
        if line.strip() and not SEGMENT.fullmatch(line.strip()):
            raise ValueError('%s line %d must be exactly one {镜头|风格|场景|动态|约束:内容} segment, one segment per line, '
                             'with no untagged text: %s' % (label, number, line.strip()[:60]))


def check_beats(label, value, duration):
    """视频提示词的动态时段：只在动态段开头用固定写法；有时段时从 0 开始首尾相接，最后一段结束于镜头时长。"""
    tenths = lambda text: round(float(text) * 10)
    beats = []
    for match in TAGGED_SEGMENT.finditer(value):
        head = BEAT.match(match.group(2)) if match.group(1) == '动态' else None
        if LOOSE_TIME.search(match.group(2)[head.end():] if head else match.group(2)):
            raise ValueError('%s: write a time range only at the start of a 动态 segment, as {动态:0-4秒：内容}; '
                             'one time range per segment: %s' % (label, match.group(0)[:60]))
        if head:
            beats.append((tenths(head.group(1)), tenths(head.group(2))))
    end = 0
    for number, (start, stop) in enumerate(beats, 1):
        if start != end or stop <= start:
            raise ValueError('%s: timed 动态 segments must start at 0 and join end to end; segment %d is %s-%s'
                             % (label, number, start / 10, stop / 10))
        end = stop
    if beats and re.fullmatch(r'\d+(?:\.\d)?|\.\d', duration.strip()) and end != tenths(duration):
        raise ValueError('%s: the last timed 动态 segment must end at the shot duration %s seconds, not %s'
                         % (label, duration.strip(), end / 10))


def human(value):
    return escape(value or '未填写', quote=True)


def static_view(model):
    chunks = ['<h1>' + human(model['title'] or '项目规格') + '</h1>',
              '<p class="lead">' + human(model['summary']) + '</p>',
              '<h2>全局设置</h2><p class="preserve">' + human(model['global_settings']) + '</p>',
              '<table><thead><tr><th>画面</th><th>镜号</th><th>时长</th><th>一句话内容</th><th>景别</th>'
              '<th>镜头运动</th><th>标记</th></tr></thead><tbody>']
    for row in model['shots']:
        thumb = ('<img src="' + escape(row['thumbnail'], quote=True) + '" alt="' + human(row['number'] + ' 参考画面') + '">'
                 if safe_thumbnail(row['thumbnail']) else '未提供')
        chunks.append('<tr><td>' + thumb + '</td><td>' + human(row['number']) + '</td><td>' +
                      human(row['duration_seconds'] + ' 秒' if row['duration_seconds'] else '待确认') + '</td><td>' +
                      human(row['content']) + '</td><td>' + human(row['shot_scale']) + '</td><td>' +
                      human(row['camera_motion']) + '</td><td>' + escape(MARKS[row['mark']]) + '</td></tr>')
    chunks.append('</tbody></table>')
    if not model['shots']:
        chunks.append('<p>尚无镜头。</p>')
    for row in model['shots']:
        chunks.append('<details><summary>' + human(row['number'] + ' · ' + (row['content'] or '详情')) + '</summary>')
        chunks.append('<h3>详细描述</h3><p class="preserve">' + human(plain_text(row['description'])) + '</p>')
        chunks.append('<h3>图片生成提示词</h3><textarea readonly rows="4" aria-label="提示词">' +
                      escape(plain_text(row['prompt']), quote=True) + '</textarea>')
        chunks.append('<h3>视频提示词</h3><textarea readonly rows="4" aria-label="视频提示词">' +
                      escape(plain_text(row['video_prompt']), quote=True) + '</textarea>')
        chunks.append('</details>')
    chunks.append('<h2>资产</h2>')
    if model['assets']:
        chunks.append('<table><thead><tr><th>ID</th><th>名称</th><th>路径</th></tr></thead><tbody>')
        for asset in model['assets']:
            chunks.append('<tr><td>' + human(asset['id']) + '</td><td>' + human(asset['name']) + '</td><td>' +
                          human(asset['path']) + '</td></tr>')
        chunks.append('</tbody></table>')
    else:
        chunks.append('<p>尚无登记的资产。</p>')
    return '\n'.join(chunks)


CSS = '''
:root{color-scheme:light;font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#26313a;background:#f4f4f1}
body{margin:0;line-height:1.6}main{max-width:1120px;margin:auto;padding:32px 24px 64px}h1{font-size:28px;margin:0 0 12px}
h2{font-size:20px;margin:28px 0 8px}h3{font-size:16px;margin:18px 0 4px}.lead,.preserve{white-space:pre-wrap;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;background:white;table-layout:fixed}th,td{border:1px solid #cbd1d3;text-align:left;padding:10px;vertical-align:top;overflow-wrap:anywhere}
th:nth-child(1){width:10%}th:nth-child(2){width:16%}th:nth-child(3){width:10%}th:nth-child(4){width:18%}img{display:block;max-width:100%;max-height:120px;object-fit:contain}
details,fieldset{background:white;border:1px solid #cbd1d3;border-radius:7px;padding:12px;margin:14px 0}summary{cursor:pointer;font-weight:700}
label{display:block;margin:12px 0}label span{display:block;font-weight:600}input,textarea{box-sizing:border-box;width:100%;font:inherit;padding:8px;border:1px solid #aab3b8;border-radius:4px}
textarea{white-space:pre-wrap;overflow-wrap:anywhere;min-height:72px}button{font:inherit;margin:6px 8px 6px 0;padding:7px 12px;cursor:pointer}
#kpl-status{padding:10px 12px;background:#e8eef0;white-space:pre-wrap}button:focus,input:focus,textarea:focus,summary:focus{outline:3px solid #34718f;outline-offset:2px}
@media(max-width:640px){main{padding:18px 12px}table{table-layout:auto}th,td{padding:5px;font-size:13px}img{max-height:70px}}
'''


def build(model):
    validate(model)
    app = APP.read_text(encoding='utf-8')
    if re.search(r'</script', app, re.I):
        raise ValueError('embedded application contains a script terminator')
    csp = "default-src 'none'; img-src 'self' data: file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'"
    return ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="' + escape(csp, quote=True) + '">'
            '<title>' + human(model['title'] or '项目规格') + '</title><style>' + CSS + '</style></head><body>'
            '<main><div id="kpl-controls" hidden><button type="button" id="kpl-edit">进入编辑</button></div>'
            '<p id="kpl-status" role="status" aria-live="polite">尚未进入编辑。</p>'
            '<p>本页可直接查看；启用 JavaScript 后可编辑。提示词也可手动选择复制。</p>'
            '<!--KPL_VIEW_START--><section id="kpl-view">' + static_view(model) + '</section><!--KPL_VIEW_END-->'
            '<section id="kpl-editor" hidden><h2>编辑当前草稿</h2><p>先选择当前项目文件，再保存；浏览器无法核实同名副本的目录。</p>'
            '<p id="kpl-link-status" role="status" aria-live="polite">尚未选择当前项目文件。</p>'
            '<div id="kpl-editor-fields"></div><button type="button" id="kpl-add">添加镜头</button>'
            '<button type="button" id="kpl-select">选择当前项目文件</button>'
            '<button type="button" id="kpl-remember" disabled>记住此文件，下次继续编辑</button>'
            '<button type="button" id="kpl-resume" hidden>确认位置未变，继续使用</button>'
            '<button type="button" id="kpl-forget" hidden>忘记文件关联</button>'
            '<button type="button" id="kpl-save">保存到所选文件</button>'
            '<button type="button" id="kpl-download">另存一份副本</button>'
            '<button type="button" id="kpl-cancel">取消编辑</button></section></main>'
            '<!--KPL_DATA_START--><script type="application/json" id="kpl-authoritative-data">' + json_for_script(model) + '</script><!--KPL_DATA_END-->'
            '<script id="kpl-application">' + app + '</script></body></html>\n')


class SpecParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capture = None
        self.scripts = {}
        self.script_types = {}
        self.view_only = False
        self.unsafe = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'iframe', 'object', 'embed', 'base', 'link'} or any(k.lower().startswith('on') or k.lower() == 'srcdoc' for k in attrs):
            self.unsafe.append(tag)
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            self.unsafe.append(tag)
        if tag == 'meta' and attrs.get('name', '').lower() == 'kpl-view-only':
            self.view_only = True
        if tag == 'script':
            name = attrs.get('id')
            if name not in {'kpl-authoritative-data', 'kpl-application'} or name in self.scripts or 'src' in attrs:
                self.unsafe.append('script')
            self.capture = name
            self.scripts[name] = ''
            self.script_types[name] = attrs.get('type', '').lower()

    def handle_data(self, data):
        if self.capture is not None:
            self.scripts[self.capture] += data

    def handle_endtag(self, tag):
        if tag == 'script':
            self.capture = None


def parse(source, require_current=True, allow_legacy=False):
    parser = SpecParser()
    parser.feed(source)
    if parser.unsafe or set(parser.scripts) != {'kpl-authoritative-data', 'kpl-application'}:
        raise ValueError('unsupported or unsafe HTML specification')
    if require_current and parser.scripts['kpl-application'] != APP.read_text(encoding='utf-8'):
        raise ValueError('HTML application code differs from the current KPL template')
    model = json.loads(parser.scripts['kpl-authoritative-data'], object_pairs_hook=unique_object,
                       parse_constant=lambda s: (_ for _ in ()).throw(ValueError('invalid number: ' + s)))
    if allow_legacy and isinstance(model, dict) and model.get('schema') == 'kpl-user-spec-v1':
        return validate_legacy(model)
    return validate(model)


def load(path, require_current=True, allow_legacy=False):
    return parse(Path(path).read_text(encoding='utf-8-sig'), require_current, allow_legacy)


def is_view_only(path):
    parser = SpecParser()
    parser.feed(Path(path).read_text(encoding='utf-8-sig'))
    return parser.view_only or parser.script_types.get('kpl-application') == 'application/x-kpl-inert'


def load_document(path, allow_legacy=True):
    """Read exactly the selected HTML or native file without executing its contents."""
    path = Path(path)
    if path.suffix.lower() == '.html':
        return load(path, require_current=False, allow_legacy=allow_legacy)
    if path.suffix.lower() != '.kpldoc':
        raise ValueError('unsupported specification format')
    raw = path.read_bytes()
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('native specification is too large')
    model = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object,
                       parse_constant=lambda s: (_ for _ in ()).throw(ValueError('invalid number: ' + s)))
    if allow_legacy and isinstance(model, dict) and model.get('schema') == 'kpl-user-spec-v1':
        return validate_legacy(model)
    return validate(model)


def document_text(model):
    """Text content for matching recorded excerpts; not proof the editor was viewed."""
    parts = [model['title'], model['summary'], model['global_settings']]
    for row in model['shots']:
        parts.extend(plain_text(v) for v in row.values() if isinstance(v, str))
    for asset in model.get('assets', []):
        parts.extend(asset.values())
    return '\n'.join(parts)


@contextmanager
def native_writer_lock(path):
    """Share the editor's named mutex for native document writes on Windows."""
    if os.name != 'nt':
        yield
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = 'Local\\KPLDocument-' + digest(str(Path(path).absolute()).upper().encode('utf-8'))
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), 'cannot create document mutex')
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 5000) in (0, 0x80)
        if not acquired:
            raise ValueError('document is being saved; no write')
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def replace_marked(source, start, end, inner):
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError('missing or ambiguous generated section; no write')
    left = source.index(start) + len(start)
    right = source.index(end)
    if right < left:
        raise ValueError('invalid generated section order; no write')
    return source[:left] + inner + source[right:]


def apply_changes(root, relative, expected, patch):
    """Apply explicit old/new fields by stable ID; caller must have project write authority."""
    import project_paths as paths
    root, _ = paths.read_project(root)
    spec = paths.ensure_beneath(root, relative)
    with native_writer_lock(spec):
        return _apply_changes_locked(root, relative, expected, patch)


def _apply_changes_locked(root, relative, expected, patch):
    import project_paths as paths
    root, marker = paths.read_project(root)
    selected = marker.get('current_spec') or {}
    spec = paths.ensure_beneath(root, relative)
    if (spec.suffix.lower() not in {'.html', '.kpldoc'} or selected.get('path') != spec.relative_to(root).as_posix()
            or selected.get('sha256') != expected):
        raise ValueError('selected specification/version differs; no write')
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('expected sha256 is required')
    before = spec.read_bytes()
    if digest(before) != expected:
        raise ValueError('specification changed since it was read; no write')
    if spec.suffix.lower() == '.html':
        model = parse(before.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n'))
    else:
        model = json.loads(before.decode('utf-8-sig'), object_pairs_hook=unique_object,
                           parse_constant=lambda s: (_ for _ in ()).throw(ValueError('invalid number: ' + s)))
        validate(model)  # v1 is intentionally read-only
    original_model = copy.deepcopy(model)
    if model['status'] == 'final' or model['confirmation'] == 'confirmed':
        raise ValueError('confirmed/final version is locked; create a draft version instead')
    operations = patch.get('operations') if isinstance(patch, dict) else None
    if not isinstance(operations, list) or not operations:
        raise ValueError('at least one explicit operation is required')
    changed, timed = [], set()
    for operation in operations:
        if not isinstance(operation, dict) or operation.get('target') not in {'project', 'shot', 'asset'}:
            raise ValueError('invalid operation')
        action = operation.get('action', 'set')
        if operation['target'] == 'project':
            field = operation.get('field')
            if action != 'set' or field not in {'title', 'summary', 'global_settings', 'layout_aspect'}:
                raise ValueError('invalid project field')
            if not isinstance(operation.get('old'), str) or not isinstance(operation.get('new'), str) or model[field] != operation['old']:
                raise ValueError('project field changed or missing old/new value')
            model[field] = operation['new']
            changed.append('project:' + field)
            continue
        ident = operation.get('id')
        if not isinstance(ident, str) or not ident:
            raise ValueError('stable shot or asset ID required')
        kind = operation['target']
        rows = model['shots'] if kind == 'shot' else model['assets']
        fields = FIELDS + ('layout_camera',) if kind == 'shot' else ('name', 'path')
        matches = [row for row in rows if row['id'] == ident]
        if action == 'add':
            row = operation.get('new')
            if matches or operation.get('old') is not None or not isinstance(row, dict) or row.get('id') != ident:
                raise ValueError('new ' + kind + ' conflicts with existing ID')
            if (kind == 'shot' and row.get('mark')
                    and (not isinstance(operation.get('authorization_ref'), str)
                         or not operation['authorization_ref'].strip())):
                raise ValueError('a new user mark needs its explicit authorization source')
            if kind == 'shot':
                for field in SEGMENTED_FIELDS:
                    if isinstance(row.get(field), str):
                        check_segments(ident + ' ' + field, row[field])
                timed.add(ident)
            rows.append(row)
        elif action == 'remove':
            if operation.get('deletion_authorized') is not True or not isinstance(operation.get('authorization_ref'), str) or not operation['authorization_ref'].strip():
                raise ValueError('removing a ' + kind + ' needs explicit user authorization and its source')
            if len(matches) != 1 or operation.get('old') != matches[0] or operation.get('new') is not None:
                raise ValueError(kind + ' changed since it was read')
            rows.remove(matches[0])
        elif action == 'set':
            field = operation.get('field')
            if (kind == 'shot' and field == 'mark' and operation.get('old') != operation.get('new')
                    and (not isinstance(operation.get('authorization_ref'), str)
                         or not operation['authorization_ref'].strip())):
                raise ValueError('changing a user mark needs its explicit authorization source')
            # layout_camera 的旧值/新值是对象或 null，其余字段是文本；新值的结构由最后的 validate 检查
            kind_ok = (lambda v: v is None or isinstance(v, dict)) if field == 'layout_camera' else (lambda v: isinstance(v, str))
            if (len(matches) != 1 or field not in fields or not kind_ok(operation.get('old'))
                    or not kind_ok(operation.get('new')) or matches[0][field] != operation['old']):
                raise ValueError(kind + ' field changed or is invalid')
            if kind == 'shot' and field in SEGMENTED_FIELDS and operation['new'] != operation['old']:
                check_segments(ident + ' ' + field, operation['new'])
            if kind == 'shot' and field in ('video_prompt', 'duration_seconds') and operation['new'] != operation['old']:
                timed.add(ident)
            matches[0][field] = operation['new']
        else:
            raise ValueError('unsupported ' + kind + ' operation')
        changed.append(kind + ':' + ident)
    # 时段与镜头时长要一起对得上：改其中一个时，另一个也要在同一批操作里改好
    for row in model['shots']:
        if row['id'] in timed and isinstance(row.get('video_prompt'), str) and isinstance(row.get('duration_seconds'), str):
            check_beats(row['id'] + ' video_prompt', row['video_prompt'], row['duration_seconds'])
    if 'timeline' in model:
        # 已授权移除的镜头不再占主轨格子，它关联的视频随之解除
        shot_ids = {row['id'] for row in model['shots']}
        main = model['timeline']['video'][0] if isinstance(model['timeline'], dict) and model['timeline'].get('video') else None
        if main and isinstance(main.get('clips'), list):
            dropped = [clip for clip in main['clips'] if isinstance(clip, dict) and clip.get('shot_id') not in shot_ids]
            main['clips'] = [clip for clip in main['clips'] if clip not in dropped]
            changed.extend('timeline:' + str(clip.get('id')) for clip in dropped)
    if model == original_model:
        return {'ok': True, 'spec': relative, 'sha256': expected, 'affected': [], 'note': '没有实际变化，未写入。'}
    model['revision'] += 1
    validate(model)
    if spec.suffix.lower() == '.kpldoc':
        updated = json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    else:
        source = before.decode('utf-8-sig')
        updated = replace_marked(source, '<!--KPL_DATA_START-->', '<!--KPL_DATA_END-->',
            '<script type="application/json" id="kpl-authoritative-data">' + json_for_script(model) + '</script>')
        updated = replace_marked(updated, '<!--KPL_VIEW_START-->', '<!--KPL_VIEW_END-->',
                                 '<section id="kpl-view">' + static_view(model) + '</section>')
        if sum(1 for _ in re.finditer(r'<title>.*?</title>', updated, re.S | re.I)) != 1:
            raise ValueError('missing or ambiguous document title')
        updated = re.sub(r'<title>.*?</title>', lambda _: '<title>' + human(model['title'] or '项目规格') + '</title>',
                         updated, count=1, flags=re.S | re.I)
    payload = updated.encode('utf-8')
    if len(payload) > 5 * 1024 * 1024:
        raise ValueError('specification is too large; no write')
    tmpdir = paths.ensure_beneath(root, '.kpl/tmp', directory=True)
    fd, tmpname = tempfile.mkstemp(prefix='spec-', suffix='.html', dir=tmpdir)
    temp = Path(tmpname)
    committed = False
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
        if digest(spec.read_bytes()) != expected:
            raise ValueError('specification changed before write; no write')
        if paths.read_project(root)[1].get('current_spec') != selected:
            raise ValueError('current selection changed before write; no write')
        os.replace(temp, spec)
        committed = True
        actual = digest(spec.read_bytes())
        if actual != digest(payload):
            raise ValueError('write completed but readback differs; outcome uncertain')
    except Exception as exc:
        if committed:
            raise ValueError('写入已发生但结果未确认；先核对文件，勿直接重试。') from exc
        raise
    finally:
        if temp.exists() and temp.parent.resolve() == tmpdir and temp.name.startswith('spec-'):
            temp.unlink()
    return {'ok': True, 'spec': relative, 'sha256': actual, 'affected': sorted(set(changed)),
            'note': '用户规格已写入；机器状态和相关旧验收需复核，未自动重新确认。'}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    reader = sub.add_parser('read', help='只读解析 HTML 或 .kpldoc 规格，不运行页面脚本')
    reader.add_argument('spec', type=Path)
    update = sub.add_parser('apply', help='按稳定 ID 和明确的旧/新值更新用户规格')
    update.add_argument('--project', type=Path, required=True)
    update.add_argument('--spec', required=True, help='相对项目根目录的用户规格路径')
    update.add_argument('--expected-sha256', required=True)
    update.add_argument('--patch', type=Path, required=True, help='内部 JSON 操作清单')
    args = parser.parse_args()
    try:
        if args.command == 'read':
            result = {'ok': True, 'model': load_document(args.spec),
                      'sha256': digest(args.spec.read_bytes()), 'note': '只读解析；未执行脚本或证明页面可见。'}
        else:
            patch = json.loads(args.patch.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object)
            result = apply_changes(args.project, args.spec, args.expected_sha256, patch)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
