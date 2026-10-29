"""Create and exchange prototype documents; never overwrite a destination."""
from pathlib import Path
import argparse
import json
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))
import html_spec


def read_doc(path):
    raw = Path(path).read_bytes()
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('规格文件过大。')
    return validate_native(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=html_spec.unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid number: ' + value))))


def validate_native(model):
    """原生文档与 HTML 规格共用同一份 v2 契约（含字段集合与数值范围）。"""
    return html_spec.validate(model)


def write_new(path, payload):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description='KPL 原型文档：创建、导入 HTML 或导出 HTML 副本；不覆盖现有文件。')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('new'); create.add_argument('output', type=Path); create.add_argument('--title', required=True)
    imp = sub.add_parser('from-html'); imp.add_argument('source', type=Path); imp.add_argument('output', type=Path)
    exp = sub.add_parser('to-html'); exp.add_argument('source', type=Path); exp.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'new': model = html_spec.new_model(args.title)
    elif args.command == 'from-html': model = html_spec.load(args.source, require_current=False)
    else: model = read_doc(args.source)
    validate_native(model)
    if args.command == 'to-html':
        if args.output.suffix.lower() != '.html': raise ValueError('导出目标必须是 .html。')
        html = html_spec.build(model).replace('<script id="kpl-application">', '<script type="application/x-kpl-inert" id="kpl-application">', 1)
        html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta name="kpl-view-only" content="true">', 1)
        html = html.replace('<main>', '<main><p>这是独立查看副本，不会自动同步原规格。</p>', 1)
        write_new(args.output, html)
    else:
        if args.output.suffix.lower() != '.kpldoc': raise ValueError('文档目标必须是 .kpldoc。')
        write_new(args.output, json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, TypeError) as exc: print(str(exc), file=sys.stderr); raise SystemExit(2)
