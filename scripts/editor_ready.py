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
PACKAGED_EXE = EDITOR / 'bin' / 'win-x64' / 'KPL规格编辑器.exe'


def runtime_registered(versions):
    """The official Evergreen Runtime registration accepts a nonzero version."""
    for version in versions:
        if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version.strip()):
            if any(int(part) for part in version.strip().split('.')):
                return True
    return False


def command_executable(command):
    """Accept an unambiguous quoted executable and one document argument only."""
    match = re.fullmatch(r'\s*"([^"]+\.exe)"\s+"?%1"?\s*', command or '', re.I)
    return match.group(1) if match else None


def command_target(command):
    path = command_executable(command)
    return os.path.normcase(os.path.abspath(path)) if path else None


def pristine_owned(snapshot, old_exe):
    """Recognize only the exact registration written by this KPL prototype."""
    if not old_exe:
        return False
    expected = '"' + os.path.abspath(old_exe) + '" "%1"'
    expected_keys = {
        'extension': ({'': 'KPL.SpecDocument.Prototype.1'}, set()),
        'program': ({'': 'KPL 规格文档（原型）', 'KplPrototypeOwner': 'KPL-native-prototype-v1'}, {'shell'}),
        'shell': ({}, {'open'}),
        'open': ({}, {'command'}),
        'command': ({'': expected}, set()),
    }
    for name, (values, subkeys) in expected_keys.items():
        entry = snapshot.get(name)
        if not entry or entry.get('values') != values or set(entry.get('subkeys', [])) != subkeys:
            return False
    return True


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
    if info.get('user_choice') or info.get('user_choice_present'):
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

    def snapshot(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
                return {'values': {winreg.EnumValue(key, i)[0]: winreg.EnumValue(key, i)[1]
                                   for i in range(values)},
                        'subkeys': [winreg.EnumKey(key, i) for i in range(subkeys)]}
        except FileNotFoundError:
            return None

    ext = r'Software\Classes\.kpldoc'
    prog = r'Software\Classes\KPL.SpecDocument.Prototype.1'
    explorer = r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.kpldoc'
    user_choice_key = explorer + r'\UserChoice'
    user_choice_present = key_exists(winreg.HKEY_CURRENT_USER, user_choice_key)
    user_choice = value(winreg.HKEY_CURRENT_USER, user_choice_key, 'ProgId')
    records = any(key_exists(base, key) for base, key in (
        (winreg.HKEY_CURRENT_USER, ext), (winreg.HKEY_LOCAL_MACHINE, ext),
        (winreg.HKEY_CURRENT_USER, prog), (winreg.HKEY_LOCAL_MACHINE, prog),
        (winreg.HKEY_CURRENT_USER, explorer)))
    release = value(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release')
    client = r'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    runtime_versions = [value(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\WOW6432Node\\' + client, 'pv'),
                        value(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\' + client, 'pv'),
                        value(winreg.HKEY_CURRENT_USER, 'SOFTWARE\\' + client, 'pv')]
    webview = runtime_registered(runtime_versions)

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
    ownership = 'not_checked'
    old_exe = command_executable(command)
    if (old_exe and command_target(command) != os.path.normcase(os.path.abspath(str(exe)))
            and not user_choice_present):
        try:
            owned_keys = {'extension': snapshot(ext), 'program': snapshot(prog),
                          'shell': snapshot(prog + r'\shell'),
                          'open': snapshot(prog + r'\shell\open'),
                          'command': snapshot(prog + r'\shell\open\command')}
            ownership = 'kpl_owned' if pristine_owned(owned_keys, old_exe) else 'other'
        except OSError:
            ownership = 'unavailable'
    return {'account': account, 'exe': exe.is_file(),
            'core': (exe.parent / 'Microsoft.Web.WebView2.Core.dll').is_file(),
            'loader': (exe.parent / 'WebView2Loader.dll').is_file(),
            'dotnet': isinstance(release, int) and release >= 528040,
            'webview': webview, 'webview_versions': runtime_versions, 'user_choice': user_choice,
            'user_choice_present': user_choice_present, 'ownership': ownership,
            'association_records': records, 'effective_command': command,
            'inspection_error': inspection_error}


def check(exe, desktop_account):
    info = probe(exe)
    status = classify(info, desktop_account, exe)
    current = command_executable(info.get('effective_command'))
    plans = {'ready': 'skip', 'needs_setup': 'authorize_first_setup',
             'protected_choice': 'user_choose_in_windows', 'wrong_user_context': 'use_desktop_account',
             'editor_missing': 'restore_packaged_editor', 'runtime_missing': 'resolve_missing_runtime',
             'inspection_error': 'inspect_error', 'target_account_required': 'identify_desktop_account'}
    if status == 'association_conflict':
        step = 'authorize_owned_rebind' if info.get('ownership') == 'kpl_owned' else 'user_choose_in_windows'
    else:
        step = plans.get(status, 'inspect_error')
    return {'status': status, 'editor': str(Path(exe).absolute()), 'checks': info,
            'plan': {'next_step': step, 'current_target': current,
                     'current_target_exists': Path(current).is_file() if current else None,
                     'requested_target': str(Path(exe).absolute()), 'check_is_read_only': True},
            'note': '只读环境检查；不能证明实际界面、编辑或保存已经通过。'}


def setup(exe, desktop_account, authorization_ref, expected_current=None):
    before = check(exe, desktop_account)
    if before['status'] == 'ready':
        return {**before, 'action': 'already_ready_no_write'}
    if before['status'] not in ('needs_setup', 'association_conflict'):
        return {**before, 'action': 'not_modified'}
    if before['status'] == 'association_conflict':
        if before.get('checks', {}).get('ownership') != 'kpl_owned':
            return {**before, 'action': 'not_modified'}
        actual = before.get('plan', {}).get('current_target')
        if not expected_current:
            return {**before, 'action': 'specific_rebind_approval_required'}
        if not actual or os.path.normcase(os.path.abspath(str(expected_current))) != os.path.normcase(os.path.abspath(actual)):
            return {**before, 'status': 'association_changed', 'action': 'not_modified'}
    if not authorization_ref or not authorization_ref.strip():
        return {**before, 'status': 'authorization_required', 'action': 'not_modified'}
    if not REGISTRATION.is_file():
        return {**before, 'status': 'setup_script_missing', 'action': 'not_modified'}
    rebind = before['status'] == 'association_conflict'
    command = ['powershell.exe', '-NoProfile', '-File', str(REGISTRATION),
               '-Action', 'Rebind' if rebind else 'Install', '-Executable', str(Path(exe).absolute())]
    if rebind:
        command.extend(['-ExpectedExecutable', actual, '-ConfirmRebind'])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=30)
    except subprocess.TimeoutExpired:
        return {**before, 'status': 'setup_unconfirmed', 'action': 'inspect_before_retry',
                'detail': '关联操作超时；执行结果未知，先检查当前实际状态。'}
    except PermissionError as exc:
        return {**before, 'status': 'setup_denied', 'action': 'not_modified',
                'detail': str(exc)}
    except OSError as exc:
        return {**before, 'status': 'setup_unavailable', 'action': 'not_modified',
                'detail': str(exc)}
    try:
        after = check(exe, desktop_account)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {**before, 'status': 'setup_unconfirmed', 'action': 'inspect_before_retry',
                'detail': '执行后无法核对实际关联：' + str(exc)}
    if completed.returncode != 0:
        return {**after, 'status': 'setup_unconfirmed', 'action': 'inspect_before_retry',
                'detail': (completed.stderr or completed.stdout).strip()}
    return {**after, 'action': ('rebound' if rebind else 'installed') if after['status'] == 'ready'
            else 'inspect_before_retry'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'setup'))
    parser.add_argument('--exe', type=Path, default=PACKAGED_EXE, help='默认使用随技能打包的共享编辑器；可指定自编译程序')
    parser.add_argument('--desktop-account', required=True, help='桌面用户的 Windows 账户，格式同 whoami')
    parser.add_argument('--authorization-ref', help='setup 时用户授权的来源；文字本身不证明授权')
    parser.add_argument('--expected-current', type=Path, help='只在用户具体同意替换 KPL 自有关联时传入原编辑器路径')
    args = parser.parse_args(argv)
    try:
        result = (check(args.exe, args.desktop_account) if args.action == 'check' else
                  setup(args.exe, args.desktop_account, args.authorization_ref, args.expected_current))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'ready' else 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'inspection_error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
