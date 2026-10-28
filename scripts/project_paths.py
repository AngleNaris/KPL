"""Path rules shared by KPL's project publisher and index verifiers."""
from pathlib import Path
import json
import re
import unicodedata

SCHEMA = 'kpl-project-v1'
USER_DIRS = ('01_项目说明', '02_导演方案', '03_分镜设计', '04_提示词',
             '05_参考资产', '06_审查报告', '07_预演交付')
FILE_RE = re.compile(r'(?P<number>0[1-9]|[1-9][0-9])_(?P<name>[\u3400-\u9fff]+)'
                     r'(?:_id(?P<id>[A-Za-z0-9-]+))?(?:_p(?P<page>[0-9]{3}))?'
                     r'_v(?P<version>[0-9]{3})_(?P<status>draft|review|final)'
                     r'\.(?P<ext>[A-Za-z0-9]{1,10})\Z')
DIR_RE = re.compile(r'(?:0[1-9]|[1-9][0-9])_[\u3400-\u9fff]+\Z')


def file_parts(name):
    if not isinstance(name, str) or len(name) > 180 or unicodedata.normalize('NFC', name) != name:
        return None
    match = FILE_RE.fullmatch(name)
    return match if match and match['version'] != '000' and match['page'] != '000' else None


def ensure_beneath(root, relative, *, directory=False):
    """Resolve a project-relative path; reject traversal and existing link components."""
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or rel.root or not rel.parts or any(part in {'', '.', '..'} for part in rel.parts):
        raise ValueError('project path must be a non-escaping relative path')
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink() or (hasattr(cur, 'is_junction') and cur.is_junction()):
            raise ValueError('project path contains a link or junction')
    resolved = cur.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('project path escapes its root')
    if directory and not resolved.is_dir():
        raise ValueError('required project directory does not exist')
    return resolved


def read_project(root):
    root = Path(root).resolve()
    marker = ensure_beneath(root, '.kpl/project.json')
    try:
        obj = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'not an initialized KPL project: {exc}') from exc
    if not isinstance(obj, dict) or obj.get('schema') != SCHEMA:
        raise ValueError('not an initialized KPL project')
    return root, obj


def project_index_root(index_path, index):
    """Return (root, public directory) for a project index; legacy returns None."""
    binding = index.get('project_output')
    if binding is None:
        return None
    index_path = Path(index_path).resolve()
    if (not isinstance(binding, dict) or binding.get('version') != 1 or
            index_path.name != 'index.json' or len(index_path.parents) < 4 or
            index_path.parents[1].name != 'index' or index_path.parents[2].name != '.kpl'):
        raise ValueError('invalid project index location or binding')
    root, _ = read_project(index_path.parents[3])
    if ensure_beneath(root, index_path.relative_to(root)) != index_path:
        raise ValueError('unsafe project index path')
    public_name = binding.get('directory')
    allowed = '06_审查报告' if index.get('schema') == 'kpl-review-board-v1' else '07_预演交付'
    if public_name != allowed:
        raise ValueError('invalid project output directory')
    return root, ensure_beneath(root, public_name, directory=True)


def output_file(index_path, index, name):
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('output filename must be a single component')
    info = project_index_root(index_path, index)
    if info is None:
        base = Path(index_path).resolve().parent
        result = (base / name).resolve()
        if not result.is_relative_to(base):
            raise ValueError('output path escapes index directory')
        return result
    root, public = info
    if file_parts(name) is None:
        raise ValueError('invalid user-facing output name')
    return ensure_beneath(root, (public / name).relative_to(root))


def input_file(index_path, index, name):
    p = Path(name)
    if p.is_absolute():
        return p
    info = project_index_root(index_path, index)
    if info is None:
        return p
    return ensure_beneath(info[0], p)


def selection_issues(index_path, index):
    info = project_index_root(index_path, index)
    if info is None:
        return []
    _, marker = read_project(info[0])
    expected = index['project_output'].get('spec')
    actual = marker.get('current_spec')
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return [{'kind': 'current_spec', 'reason': 'missing explicit selection'}]
    if any(expected.get(k) != actual.get(k) for k in ('path', 'sha256')):
        return [{'kind': 'current_spec', 'reason': 'selected version changed'}]
    return []
