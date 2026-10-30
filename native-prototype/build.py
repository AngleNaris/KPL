"""Build the native editor: inline ui/ into one trusted HTML template, then compile the host
with the pre-existing local WebView2 components."""
from pathlib import Path
from html import escape
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
UI = HERE / 'ui'
ASSETS = HERE / 'assets'
sys.path.insert(0, str(HERE.parent / 'scripts'))
from html_spec import replace_marked
from editor_bundle import input_hashes, write_manifest, sha

DIST = HERE / 'dist'
PACKAGED = HERE / 'bin' / 'win-x64'
CORE = PACKAGED / 'Microsoft.Web.WebView2.Core.dll'
LOADER = PACKAGED / 'WebView2Loader.dll'
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
ICON = ASSETS / 'kpl.ico'
# Hashes of the distributed, Microsoft-signed SDK 1.0.3179.45 files; alternate
# inputs remain supported but their claimed version is deliberately unverified.
SDK_HASHES = ('37f37a80a2ce89388b30c38185a9ba3af1727a2ebbb59698e13725db770c9816',
              '01f0a861a802233411fe0d406988c211794c0f58ce2edb62ff97c31f5b1aa2fe')
# 图片只来自宿主映射的只读缩略图缓存（原图不让页面直接载入）；不允许任何网络或文件访问
CSP = ("default-src 'none'; img-src https://kpl-thumbs.local; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'")


def template():
    html = (UI / 'index.html').read_text(encoding='utf-8')
    css = '\n'.join((UI / name).read_text(encoding='utf-8') for name in ('tokens.css', 'app.css'))
    app = (UI / 'app.js').read_text(encoding='utf-8')
    if re.search(r'</(?:style|script)', css + app, re.I):
        raise ValueError('inlined UI contains a style/script terminator')
    if html.count('<meta charset="utf-8">') != 1:
        raise ValueError('UI shell must contain exactly one charset meta tag')
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'
                        + escape(CSP, quote=True) + '">')
    html = replace_marked(html, '<!--KPL_STYLE_START-->', '<!--KPL_STYLE_END-->', '<style>' + css + '</style>')
    html = replace_marked(html, '<!--KPL_DATA_START-->', '<!--KPL_DATA_END-->',
        '<script type="application/json" id="kpl-authoritative-data">__KPL_NATIVE_DATA_PLACEHOLDER__</script>'
        '<script type="application/json" id="kpl-prefs">__KPL_NATIVE_PREFS_PLACEHOLDER__</script>')
    return replace_marked(html, '<!--KPL_SCRIPT_START-->', '<!--KPL_SCRIPT_END-->', '<script>' + app + '</script>')


def main(argv=None):
    parser = argparse.ArgumentParser(description='从现有本地组件构建共享编辑器，不下载依赖，也不覆盖现有输出。')
    parser.add_argument('--csc', type=Path, default=CSC, help='现有 C# 编译器')
    parser.add_argument('--core', type=Path, default=CORE, help='现有 WebView2 Core .NET DLL')
    parser.add_argument('--loader', type=Path, default=LOADER, help='相匹配的 x64 WebView2Loader.dll')
    parser.add_argument('--out-dir', type=Path, default=DIST, help='不存在或为空的输出目录')
    args = parser.parse_args(argv)
    for path in (args.csc, args.core, args.loader, ICON):
        if not path.is_file(): raise SystemExit('Missing existing tool/component: ' + str(path))
    inputs = input_hashes()
    out = args.out_dir.absolute()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SystemExit('输出目录不是空目录；不会覆盖，请选择新目录：' + str(out))
    out.mkdir(parents=True, exist_ok=True)
    # The generated resource is temporary, not a second copy of project data.
    with tempfile.TemporaryDirectory(prefix='kpl-build-') as temp:
        resource = Path(temp) / 'editor.html'
        resource.write_text(template(), encoding='utf-8', newline='\n')
        command = [str(args.csc), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
            '/out:' + str(out / 'KPL规格编辑器.exe'), '/win32icon:' + str(ICON), '/reference:' + str(args.core),
            '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll',
            '/resource:' + str(resource) + ',KplNativeTemplate',
            '/resource:' + str(ICON) + ',KplIcon',
            str(HERE / 'src' / 'Host.cs'), str(HERE / 'src' / 'DocumentSession.cs')]
        subprocess.run(command, check=True)
    shutil.copy2(args.core, out / 'Microsoft.Web.WebView2.Core.dll')
    shutil.copy2(args.loader, out / 'WebView2Loader.dll')
    sdk = 'custom or unverified'
    if SDK_HASHES and (sha(args.core), sha(args.loader)) == SDK_HASHES:
        sdk = 'Microsoft.Web.WebView2 1.0.3179.45'
    write_manifest(out, inputs, sdk)
    print('Built:', out / 'KPL规格编辑器.exe')


if __name__ == '__main__': main()
