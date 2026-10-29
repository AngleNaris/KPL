#!/usr/bin/env python3
"""Check a native KPL editor and its per-user association; setup is explicit only."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EDITOR = Path(__file__).resolve().parent.parent / 'native-prototype'
REGISTRATION = EDITOR / 'file-association.ps1'


def command_target(command):
    """Accept an unambiguous quoted executable and one document argument only."""
    match = re.fullmatch(r'\s*"([^"]+\.exe)"\s+"?%1"?\s*', command or '', re.I)
    return os.path.normcase(os.path.abspath(match.group(1))) if match else None


def classify(info, desktop_account, exe):
    if not desktop_account or not info.get('account'):
        return 'target_account_required'
    if desktop_account.casefold() != info['account'].casefold():
        return 'wrong_user_context'
    if info.get('inspection_error'):
        return 'inspection_error'
    if not info.get('exe') or not info.get('core') or not info.get('loader'):
        return 'editor_missing'
    if not info.get('dotnet') or not info.get('webview'):
        return 'runtime_missing'
    expected = os.path.normcase(os.path.abspath(str(exe)))
    target = command_target(info.get('effective_command'))
    if target == expected:
        return 'ready'
    if info.get('user_choice'):
        return 'protected_choice'
    if target or info.get('association_records'):
        return 'association_conflict'
    return 'needs_setup'


def probe(exe):
    if os.name != 'nt':
        raise OSError('only Windows can inspect the native editor')
    import winreg
    exe = Path(exe).absolute()
    account = subprocess.run(['whoami'], check=True, capture_output=True, text=True).stdout.strip()

    def key_exists(base, path):
        try:
            with winreg.OpenKey(base, path): return True
        except FileNotFoundError: return False

    def value(base, path, name):
        try:
            with winreg.OpenKey(base, path) as key: return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError: return None

    ext = r'Software\Classes\.kpldoc'
    prog = r'Software\Classes\KPL.SpecDocument.Prototype.1'
    explorer = r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.kpldoc'
    user_choice = value(winreg.HKEY_CURRENT_USER, explorer + r'\UserChoice', 'ProgId')
    records = any(key_exists(base, key) for base, key in (
        (winreg.HKEY_CURRENT_USER, ext), (winreg.HKEY_LOCAL_MACHINE, ext),
        (winreg.HKEY_CURRENT_USER, prog), (winreg.HKEY_LOCAL_MACHINE, prog),
        (winreg.HKEY_CURRENT_USER, explorer)))
    release = value(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release')
    webview_root = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft/EdgeWebView/Application'
    webview = any(p.is_file() for p in webview_root.glob('*/msedgewebview2.exe'))

    # Ask Windows for the effective command; registry entries alone may be overridden by UserChoice.
    shell = ctypes.WinDLL('shlwapi', use_last_error=True)
    query = shell.AssocQueryStringW
    query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
                      ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    query.restype = ctypes.c_long
    size = ctypes.c_uint32(0)
    query(0, 1, '.kpldoc', None, None, ctypes.byref(size))
    command = None
    inspection_error = None
    if size.value:
        buffer = ctypes.create_unicode_buffer(size.value)
        result = query(0, 1, '.kpldoc', None, buffer, ctypes.byref(size))
        if result == 0:
            command = buffer.value
        elif records:
            inspection_error = 'Windows 无法解析当前打开命令。'
    elif records:
        inspection_error = '存在关联记录，但 Windows 未返回有效打开命令。'
    return {'account': account, 'exe': exe.is_file(),
            'core': (exe.parent / 'Microsoft.Web.WebView2.Core.dll').is_file(),
            'loader': (exe.parent / 'WebView2Loader.dll').is_file(),
            'dotnet': isinstance(release, int) and release >= 528040,
            'webview': webview, 'user_choice': user_choice,
            'association_records': records, 'effective_command': command,
            'inspection_error': inspection_error}


def check(exe, desktop_account):
    info = probe(exe)
    return {'status': classify(info, desktop_account, exe), 'editor': str(Path(exe).absolute()),
            'checks': info, 'note': '只读环境检查；不能证明实际界面、编辑或保存已经通过。'}


def setup(exe, desktop_account, authorization_ref):
    before = check(exe, desktop_account)
    if before['status'] == 'ready':
        return {**before, 'action': 'already_ready_no_write'}
    if before['status'] != 'needs_setup':
        return {**before, 'action': 'not_modified'}
    if not authorization_ref or not authorization_ref.strip():
        return {**before, 'status': 'authorization_required', 'action': 'not_modified'}
    if not REGISTRATION.is_file():
        return {**before, 'status': 'setup_script_missing', 'action': 'not_modified'}
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(REGISTRATION),
                                '-Action', 'Install', '-Executable', str(Path(exe).absolute())],
                               capture_output=True, text=True, errors='replace')
    after = check(exe, desktop_account)
    if completed.returncode != 0:
        return {**after, 'status': 'setup_unconfirmed', 'action': 'inspect_before_retry',
                'detail': (completed.stderr or completed.stdout).strip()}
    return {**after, 'action': 'installed' if after['status'] == 'ready' else 'inspect_before_retry'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'setup'))
    parser.add_argument('--exe', type=Path, required=True, help='共享编辑器的实际 exe 路径')
    parser.add_argument('--desktop-account', required=True, help='桌面用户的 Windows 账户，格式同 whoami')
    parser.add_argument('--authorization-ref', help='setup 时用户授权的来源；文字本身不证明授权')
    args = parser.parse_args(argv)
    try:
        result = (check(args.exe, args.desktop_account) if args.action == 'check' else
                  setup(args.exe, args.desktop_account, args.authorization_ref))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'ready' else 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'inspection_error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
