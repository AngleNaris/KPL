#!/usr/bin/env python3
"""Record and check the source/build inputs and files of the native editor bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / 'native-prototype/bin/win-x64'
INPUTS = (
    'native-prototype/build.py', 'scripts/editor_bundle.py', 'scripts/html_spec.py',
    'native-prototype/src/Host.cs', 'native-prototype/src/DocumentSession.cs',
    'native-prototype/ui/index.html', 'native-prototype/ui/tokens.css',
    'native-prototype/ui/app.css', 'native-prototype/ui/app.js',
    'native-prototype/assets/kpl.ico',
)
OUTPUTS = ('KPL规格编辑器.exe', 'Microsoft.Web.WebView2.Core.dll', 'WebView2Loader.dll')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes():
    return {name: sha(ROOT / name) for name in INPUTS}


def write_manifest(bundle, before, sdk_label):
    if input_hashes() != before:
        raise RuntimeError('Build inputs changed during compilation; do not distribute this output.')
    data = {'schema': 'kpl-editor-bundle-v1', 'platform': 'windows-x64',
            'sdk': sdk_label, 'document_schema': 'kpl-user-spec-v2',
            'build_inputs_sha256': before,
            'files_sha256': {name: sha(bundle / name) for name in OUTPUTS}}
    (bundle / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                                          encoding='utf-8', newline='\n')


def verify(bundle):
    try:
        data = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
        if (data.get('schema') != 'kpl-editor-bundle-v1' or
            data.get('platform') != 'windows-x64' or
            set(data.get('build_inputs_sha256', {})) != set(INPUTS) or
            set(data.get('files_sha256', {})) != set(OUTPUTS)):
            return {'status': 'invalid_manifest'}
        issues = []
        for name, expected in data['build_inputs_sha256'].items():
            if not (ROOT / name).is_file() or sha(ROOT / name) != expected:
                issues.append({'kind': 'source', 'path': name})
        for name, expected in data['files_sha256'].items():
            if not (bundle / name).is_file() or sha(bundle / name) != expected:
                issues.append({'kind': 'binary', 'path': name})
        return {'status': 'changed' if issues else 'matching', 'issues': issues,
                'sdk': data.get('sdk'), 'document_schema': data.get('document_schema')}
    except (OSError, ValueError, TypeError):
        return {'status': 'invalid_manifest'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_BUNDLE,
                        help='editor directory containing manifest.json (default: packaged editor)')
    args = parser.parse_args()
    result = verify(args.bundle)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'matching' else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
