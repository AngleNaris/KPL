#!/usr/bin/env python3
"""Initialize/inspect a KPL project and publish review outputs into its user area.

Inspection and mapping are read-only. No command migrates an existing project.
Boards/layout are generated in .kpl/tmp and published without overwriting files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from fractions import Fraction
from pathlib import Path

import project_paths as paths
import html_spec

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

HERE = Path(__file__).resolve()
SKILL = HERE.parent.parent
MACHINE_DIRS = ('index', 'tmp', 'state', 'cache', 'logs', 'imports')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def init(root, spec_format='kpldoc'):
    if spec_format not in ('kpldoc', 'html'):
        raise ValueError('不支持的规格格式。')
    root = Path(root).absolute()
    if root.exists() or root.is_symlink():
        raise ValueError('项目路径已存在；不会覆盖或把已有项目视为新项目。')
    if not root.parent.is_dir():
        raise ValueError('新项目的父目录不存在。')
    root.mkdir()
    for name in paths.USER_DIRS:
        (root / name).mkdir()
    machine = root / '.kpl'
    machine.mkdir()
    for name in MACHINE_DIRS:
        (machine / name).mkdir()
    spec = root / paths.USER_DIRS[0] / ('01_项目规格_v001_draft.' + spec_format)
    model = html_spec.new_model(root.name)
    payload = (json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
               if spec_format == 'kpldoc' else html_spec.build(model))
    spec.write_text(payload, encoding='utf-8', newline='\n')
    state_path = machine / 'state' / 'project-state.json'
    state = {'version': 1, 'spec_binding': {'path_from_state': Path(os.path.relpath(spec, state_path.parent)).as_posix(),
             'sha256': sha(spec)}, 'requirements': [], 'artifacts': [], 'source_audits': [],
             'tasks': [], 'cuts': [], 'issues': []}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    marker = {'schema': paths.SCHEMA, 'current_spec': {'path': spec.relative_to(root).as_posix(),
              'sha256': sha(spec), 'selection_basis': '新项目初始化的未确认草稿'},
              'note': '本文件只引用用户区规格；不代表创作确认。'}
    (machine / 'project.json').write_text(json.dumps(marker, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'ok': True, 'project': str(root.resolve()), 'spec': str(spec.resolve()), 'state': str(state_path.resolve()),
            'spec_status': 'draft_unconfirmed', 'directories': list(paths.USER_DIRS)}


def inspect(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('项目目录不存在。')
    try:
        _, marker = paths.read_project(root)
        initialized = True
    except ValueError:
        marker, initialized = None, False
    issues, warnings, mapping = [], [], []
    if initialized:
        for name in paths.USER_DIRS:
            try:
                paths.ensure_beneath(root, name, directory=True)
            except ValueError:
                issues.append({'path': name, 'issue': 'missing_or_unsafe_directory'})
    for current, dirnames, files in os.walk(root, followlinks=False):
        current = Path(current)
        rel_dir = current.relative_to(root)
        if rel_dir.parts and rel_dir.parts[0] == '.kpl':
            dirnames[:] = []
            continue
        if current == root:
            dirnames[:] = [d for d in dirnames if d != '.kpl']
        names = set()
        seen_numbers = {}
        for name in sorted(dirnames + files):
            item = current / name
            relative = item.relative_to(root).as_posix()
            if name.casefold() in names:
                issues.append({'path': relative, 'issue': 'case_insensitive_collision'})
            names.add(name.casefold())
            if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                issues.append({'path': relative, 'issue': 'link_not_traversed'})
                if name in dirnames:
                    dirnames.remove(name)
                continue
            if item.is_dir():
                valid = bool(paths.DIR_RE.fullmatch(name))
                kind = 'directory'
            else:
                parsed = paths.file_parts(name)
                valid = parsed is not None
                kind = 'file'
                if item.suffix.lower() == '.md':
                    issues.append({'path': relative, 'issue': 'user_document_markdown'})
                if parsed and parsed['status'] == 'final':
                    warnings.append({'path': relative, 'warning': 'verify_confirmation_and_acceptance'})
            if not valid:
                issues.append({'path': relative, 'issue': 'nonstandard_' + kind})
                mapping.append({'original': relative, 'suggested_destination': None,
                                'action': '待人工确认名称和归属；保持原件与引用'})
            else:
                number, main = name.split('_', 2)[:2]
                if number in seen_numbers and seen_numbers[number] != main:
                    issues.append({'path': relative, 'issue': 'ordinal_used_by_another_name'})
                seen_numbers[number] = main
    if marker:
        selected = marker.get('current_spec') or {}
        try:
            spec = paths.ensure_beneath(root, selected['path'])
            if not spec.is_file() or sha(spec) != selected.get('sha256'):
                issues.append({'path': selected.get('path'), 'issue': 'current_spec_changed_or_missing'})
        except (KeyError, ValueError):
            issues.append({'path': None, 'issue': 'current_spec_invalid'})
    return {'ok': True, 'project': str(root), 'status': ('legacy_mapping_needed' if not initialized else
            'review_required' if issues else 'conforming'), 'issues': issues, 'warnings': warnings, 'mapping': mapping,
            'current_spec': marker.get('current_spec') if marker else None,
            'note': '只读检查；映射未执行，版本名不构成确认。'}


def selected_spec(root, marker):
    selected = marker.get('current_spec')
    if not isinstance(selected, dict) or not isinstance(selected.get('path'), str):
        raise ValueError('未明确选择当前规格。')
    spec = paths.ensure_beneath(root, selected['path'])
    if (not spec.is_relative_to(root / paths.USER_DIRS[0]) or paths.file_parts(spec.name) is None
            or spec.suffix.lower() not in {'.html', '.kpldoc', '.md'}):
        raise ValueError('当前规格须位于项目说明区；支持 .kpldoc、HTML 和只读旧 Markdown。')
    if not spec.is_file() or sha(spec) != selected.get('sha256'):
        raise ValueError('当前规格已更改或丢失；先核对并明确重新选择该版本。')
    if spec.suffix.lower() in {'.html', '.kpldoc'}:
        if spec.suffix.lower() == '.html' and html_spec.is_view_only(spec):
            raise ValueError('只读导出副本不能作为当前规格；请选择实际源文件。')
        model = html_spec.load_document(spec)
        name = paths.file_parts(spec.name)
        if model['version'] != 'v' + name['version'] or model['status'] != name['status']:
            raise ValueError('规格内版本/状态与文件名不一致；不能猜测确认。')
    return spec


def select_spec(root, relative, basis):
    root, marker = paths.read_project(root)
    if not basis.strip():
        raise ValueError('必须提供明确的版本选择依据。')
    candidate = paths.ensure_beneath(root, relative)
    if (not candidate.is_file() or not candidate.is_relative_to(root / paths.USER_DIRS[0])
            or paths.file_parts(candidate.name) is None or candidate.suffix.lower() not in {'.html', '.kpldoc'}):
        raise ValueError('所选规格须是项目说明目录中的规范命名 .kpldoc 或 HTML；旧 Markdown 只读兼容。')
    if candidate.suffix.lower() == '.html' and html_spec.is_view_only(candidate):
        raise ValueError('只读导出副本不能作为当前规格；请选择实际源文件。')
    model = html_spec.load_document(candidate)
    name = paths.file_parts(candidate.name)
    if model['version'] != 'v' + name['version'] or model['status'] != name['status']:
        raise ValueError('规格内版本/状态与文件名不一致；不能猜测确认。')
    marker_path = paths.ensure_beneath(root, '.kpl/project.json')
    lock_path = paths.ensure_beneath(root, '.kpl/selection.lock')
    fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    temp_path = None
    try:
        before = marker_path.read_bytes()
        marker = json.loads(before.decode('utf-8'))
        marker['current_spec'] = {'path': candidate.relative_to(root).as_posix(),
                                  'sha256': sha(candidate), 'selection_basis': basis}
        temp_dir = paths.ensure_beneath(root, '.kpl/tmp', directory=True)
        fd, name = tempfile.mkstemp(prefix='selection-', suffix='.json', dir=temp_dir)
        temp_path = Path(name)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(marker, f, ensure_ascii=False, indent=2)
            f.write('\n')
        if marker_path.read_bytes() != before:
            raise ValueError('项目指针被并发修改；未覆盖。')
        os.replace(temp_path, marker_path)
        return {'ok': True, 'current_spec': marker['current_spec'],
                'note': '仅更新指针；旧证据须复核，名称不表示创作确认。'}
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
        lock_path.unlink(missing_ok=True)


def relative_if_inside(root, value):
    p = Path(value).resolve()
    return p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p)


def seconds_from_frames(frames, fps):
    """Read-only conversion of a legacy count with an explicitly supplied rate."""
    if not re.fullmatch(r'(?:0|[1-9][0-9]*)', frames):
        raise ValueError('旧记录的数量必须是非负整数。')
    try:
        rate = Fraction(fps)
        if rate <= 0:
            raise ValueError
    except (ValueError, ZeroDivisionError):
        raise ValueError('必须提供已明确的正数帧率，不能猜测。') from None
    duration = Fraction(int(frames), 1) / rate
    return {'ok': True, 'seconds': round(float(duration), 9), 'seconds_exact': str(duration),
            'source': {'legacy_frames': int(frames), 'fps': str(rate)},
            'note': '只读转换，原记录未修改；需核对原始时间基及确认依据。'}


def _relative_sources(root, index):
    for key in ('manifest', 'spec'):
        if index.get(key):
            index[key]['path'] = relative_if_inside(root, index[key]['path'])
    for source in index.get('sources', []):
        source['original_path'] = relative_if_inside(root, source['original_path'])
    for cell in index.get('cells', []):
        if cell.get('source_path'):
            cell['source_path'] = relative_if_inside(root, cell['source_path'])
    for shot in index.get('shots', []):
        shot['source_path'] = relative_if_inside(root, shot['source_path'])


def _occupied_number(public, number, name):
    for existing in public.iterdir():
        match = paths.file_parts(existing.name)
        if match and match['number'] == number and match['name'] != name:
            raise ValueError(f'编号 {number} 已由其他名称使用：{existing.name}')


def publish(args):
    root, marker = paths.read_project(args.project)
    spec = selected_spec(root, marker)
    manifest = Path(args.manifest).resolve()
    raw = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(raw.get('spec'), str) or (manifest.parent / raw['spec']).resolve() != spec:
        raise ValueError('清单必须明确引用当前选定的用户规格；不能根据文件名猜版本。')
    number, version = args.number, args.version
    if not re.fullmatch(r'(?:0[1-9]|[1-9][0-9])', number) or not re.fullmatch(r'v(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})', version):
        raise ValueError('编号须为 01–99，版本须为 v001–v999。')
    is_board = args.command == 'board'
    public_name = paths.USER_DIRS[5] if is_board else paths.USER_DIRS[6]
    public = paths.ensure_beneath(root, public_name, directory=True)
    tmp = paths.ensure_beneath(root, '.kpl/tmp', directory=True)
    index_parent = paths.ensure_beneath(root, '.kpl/index', directory=True)
    title = ({'sequence': '镜头总览', 'cut': '切点对照', 'motion': '运动总览'}[args.mode]
             if is_board else ('静态预演' if args.static else '布局预演'))
    _occupied_number(public, number, title)
    work = Path(tempfile.mkdtemp(prefix='job-', dir=tmp))
    created = []
    try:
        out = work / 'result'
        script = HERE.with_name('review_boards.py' if is_board else 'layout_preview.py')
        argv = [sys.executable, '-X', 'utf8', '-B', str(script), args.mode if is_board else 'create',
                '--manifest', str(manifest), '--out', str(out)]
        if is_board:
            if args.mode == 'sequence':
                argv += ['--page-size', str(args.page_size)]
            elif args.mode == 'cut':
                if not args.left or not args.right or args.interval is None:
                    raise ValueError('切点板需要 --left、--right 和以秒表示的 --interval。')
                argv += ['--left', args.left, '--right', args.right, '--samples', str(args.frames),
                         '--interval', str(args.interval), '--cut-type', args.cut_type]
            else:
                if not args.shot or args.interval is None:
                    raise ValueError('运动板需要 --shot 和以秒表示的 --interval。')
                argv += ['--shot', args.shot, '--interval', str(args.interval)]
        elif args.static:
            argv.append('--static')
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
        if result.returncode != 0:
            raise ValueError('生成工具未完成：' + (result.stderr or result.stdout).decode('utf-8', 'replace')[-2000:])
        child = json.loads(result.stdout.decode('utf-8'))
        index = json.loads((out / 'index.json').read_text(encoding='utf-8'))
        rows = index['pages'] if is_board else [index['video']]
        if len(rows) > 999:
            raise ValueError('页数超过 p999，不能安全命名。')
        planned = []
        for pos, row in enumerate(rows, 1):
            filename = (f'{number}_{title}_p{pos:03d}_{version}_draft.png' if is_board else
                        f'{number}_{title}_{version}_draft.mp4')
            target = paths.ensure_beneath(root, (public / filename).relative_to(root))
            if target.exists() or any(x.name.casefold() == filename.casefold() for x in public.iterdir()):
                raise ValueError(f'输出文件已存在；不会覆盖：{filename}')
            source = (out / row['file']).resolve()
            if source.parent != out.resolve() or not source.is_file() or sha(source) != row['sha256']:
                raise ValueError('临时输出不完整或哈希不匹配。')
            planned.append((source, target, row, filename))
        if selected_spec(root, paths.read_project(root)[1]) != spec:
            raise ValueError('规格选择在生成期间变化；未发布。')
        for source, target, row, filename in planned:
            with source.open('rb') as src, target.open('xb') as dst:
                created.append(str(target))
                shutil.copyfileobj(src, dst)
            if sha(target) != row['sha256']:
                raise ValueError(f'发布文件哈希不匹配：{target}')
            row['file'] = filename
        _relative_sources(root, index)
        index['project_output'] = {'version': 1, 'directory': public_name,
            'spec': {'path': marker['current_spec']['path'], 'sha256': marker['current_spec']['sha256']},
            'paths_sha256': sha(HERE.with_name('project_paths.py')), 'publisher_sha256': sha(HERE)}
        index_dir = index_parent / ('board-' if is_board else 'layout-')
        index_dir = Path(str(index_dir) + uuid.uuid4().hex)
        index_dir.mkdir()
        index_path = index_dir / 'index.json'
        with index_path.open('x', encoding='utf-8', newline='\n') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
            f.write('\n')
        created.append(str(index_path))
        check = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), 'verify', str(index_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        verification = json.loads(check.stdout.decode('utf-8')) if check.stdout else {'ok': False}
        return {'ok': check.returncode == 0, 'outputs': [p[1].relative_to(root).as_posix() for p in planned],
                'index': index_path.relative_to(root).as_posix(), 'generation': child,
                'verification': verification, 'review_status': 'unverified'}
    except Exception as exc:
        if created:
            raise ValueError(f'{exc}；已写出的文件保留待核查：{created}') from exc
        raise
    finally:
        if work.exists() and work.parent.resolve() == tmp and work.name.startswith('job-'):
            shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    one = sub.add_parser('init', help='仅在不存在的路径创建新项目')
    one.add_argument('project', type=Path)
    one.add_argument('--format', choices=('kpldoc', 'html'), default='kpldoc',
                     help='当前规格格式；默认 .kpldoc，HTML 为明确选择的兼容方案')
    one = sub.add_parser('inspect', help='只读检查目录和待确认映射，不迁移')
    one.add_argument('project', type=Path)
    one = sub.add_parser('select-spec', help='显式选择当前用户区规格，不自动确认内容')
    one.add_argument('--project', type=Path, required=True)
    one.add_argument('--spec', required=True, help='项目根目录相对路径')
    one.add_argument('--basis', required=True, help='选择依据或用户决定的来源')
    one = sub.add_parser('seconds-from-frames', help='按明确帧率只读换算旧记录为秒，不修改原项目')
    one.add_argument('--frames', required=True, help='旧记录的整数数量')
    one.add_argument('--fps', required=True, help='旧记录明确的帧率，可使用分数')
    for command in ('board', 'layout'):
        one = sub.add_parser(command, help='生成并发布审片板' if command == 'board' else '生成并发布静态素材预演')
        if command == 'board':
            one.add_argument('mode', choices=('sequence', 'cut', 'motion'))
            one.add_argument('--page-size', type=int, default=9)
            one.add_argument('--left'); one.add_argument('--right'); one.add_argument('--shot')
            one.add_argument('--samples', '--frames', dest='frames', type=int, default=3, help='每侧采样画面数')
            one.add_argument('--cut-type', default='未指定'); one.add_argument('--interval', type=float, help='采样间隔（秒）')
        else:
            one.add_argument('--static', action='store_true')
        one.add_argument('--project', type=Path, required=True)
        one.add_argument('--manifest', type=Path, required=True)
        one.add_argument('--number', required=True, help='用户区排序编号，例如 01')
        one.add_argument('--version', required=True, help='明确版本，例如 v001')
    args = parser.parse_args()
    try:
        result = (init(args.project, args.format) if args.command == 'init' else inspect(args.project) if args.command == 'inspect'
                  else select_spec(args.project, args.spec, args.basis) if args.command == 'select-spec'
                  else seconds_from_frames(args.frames, args.fps) if args.command == 'seconds-from-frames' else publish(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
