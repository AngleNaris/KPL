# 把 logo-wide-src.jpg 描摹成矢量 logo-wide.svg（只用 numpy + Pillow，不依赖外部描摹工具）。
# 抗锯齿边缘先按底色还原透明度，再高斯平滑，用 marching squares 取亚像素等值线，因此边缘平滑、没有像素台阶。
# 按色相分三层：k=深橙（K），a=琥珀（P、胶片），c=奶油（L、高光）；SVG 只留 class，颜色由页面 CSS 决定。
# 用法：python -I trace_logo.py
from pathlib import Path
import math
import sys
import numpy as np
from PIL import Image, ImageChops, ImageDraw

sys.setrecursionlimit(20000)
OUT = Path(__file__).resolve().parent
EPS, SIGMA = 0.6, 1.5
FILL = {'k': '#EE7A00', 'a': '#F0A030', 'c': '#F8F0D0'}
BG = np.array([19.0, 23.0, 26.0])

rgb = np.asarray(Image.open(OUT / 'logo-wide-src.jpg').convert('RGB')).astype(float)
H, W, _ = rgb.shape
diff = rgb - BG
alpha = np.clip(diff[:, :, 0] / 220.0, 0, 1)
ratio = diff[:, :, 1] / np.maximum(diff[:, :, 0], 1)
conf = alpha > 0.75
label = {'k': conf & (ratio < 0.53), 'a': conf & (ratio >= 0.53) & (ratio < 0.8), 'c': conf & (ratio >= 0.8)}


def blur(a, s):
    r = int(3 * s) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / s) ** 2)
    k /= k.sum()
    p = np.pad(a, r, mode='edge')
    t = sum(k[i] * p[:, i:i + a.shape[1]] for i in range(2 * r + 1))
    return sum(k[i] * t[i:i + a.shape[0], :] for i in range(2 * r + 1))


def contours(f, t=0.5):
    f = np.pad(f, 1)
    inside = f > t
    case = inside[:-1, :-1] * 1 + inside[:-1, 1:] * 2 + inside[1:, 1:] * 4 + inside[1:, :-1] * 8
    plain = {1: 'LT', 2: 'TR', 3: 'LR', 4: 'RB', 6: 'TB', 7: 'LB', 8: 'LB', 9: 'TB', 11: 'RB', 12: 'LR', 13: 'TR', 14: 'LT'}
    nbr = {}
    for y, x in zip(*np.nonzero((case > 0) & (case < 15))):
        y, x, c = int(y), int(x), int(case[y, x])
        edge = {'T': ('h', y, x), 'R': ('v', y, x + 1), 'B': ('h', y + 1, x), 'L': ('v', y, x)}
        if c in (5, 10):
            center = f[y:y + 2, x:x + 2].mean() > t
            pairs = ('TR', 'LB') if (c == 5) == center else ('LT', 'RB')
        else:
            pairs = (plain[c],)
        for pr in pairs:
            a, b = edge[pr[0]], edge[pr[1]]
            nbr.setdefault(a, []).append(b)
            nbr.setdefault(b, []).append(a)

    def point(key):
        k, y, x = key
        if k == 'h':
            a, b = f[y, x], f[y, x + 1]
            return (x + (t - a) / (b - a) - 1, y - 1)
        a, b = f[y, x], f[y + 1, x]
        return (x - 1, y + (t - a) / (b - a) - 1)

    loops, seen = [], set()
    for s in nbr:
        if s in seen: continue
        loop, prev, cur = [s], s, nbr[s][0]
        seen.add(s)
        while cur != s and len(loop) < 200000:
            loop.append(cur)
            seen.add(cur)
            n = nbr[cur]
            prev, cur = cur, (n[0] if n[0] != prev else n[1])
        loops.append([point(k) for k in loop])
    return loops


def rdp(p, eps):
    if len(p) < 3: return p
    a, b = np.array(p[0]), np.array(p[-1])
    q = np.array(p[1:-1])
    d = b - a
    n = math.hypot(d[0], d[1]) or 1.0
    dist = np.abs(d[0] * (q[:, 1] - a[1]) - d[1] * (q[:, 0] - a[0])) / n
    i = int(np.argmax(dist))
    if dist[i] <= eps: return [p[0], p[-1]]
    return rdp(p[:i + 2], eps)[:-1] + rdp(p[i + 1:], eps)


def simplify(l):
    # 闭合轮廓：以离起点最远的点为界拆成两段分别化简
    k = max(range(len(l)), key=lambda i: (l[i][0] - l[0][0]) ** 2 + (l[i][1] - l[0][1]) ** 2)
    return rdp(l[:k + 1], EPS)[:-1] + rdp(l[k:] + [l[0]], EPS)[:-1]


def area(l):
    return 0.5 * sum(l[i][0] * l[(i + 1) % len(l)][1] - l[(i + 1) % len(l)][0] * l[i][1] for i in range(len(l)))


def raster(ls):
    acc = Image.new('1', (W, H), 0)
    for l in ls:
        m = Image.new('1', (W, H), 0)
        ImageDraw.Draw(m).polygon(l, fill=1)
        acc = ImageChops.logical_xor(acc, m)
    return np.asarray(acc)


total = blur(conf.astype(float), 3) + 1e-6
smooth_alpha = blur(alpha, SIGMA)
body, points, union = [], [], np.zeros((H, W), bool)
for name in 'kac':
    share = np.clip(blur(label[name].astype(float), 3) / total, 0, 1)
    field = smooth_alpha * share
    ls = [simplify(l) for l in contours(field)]
    ls = [l for l in ls if len(l) >= 3 and abs(area(l)) >= 20]
    r, ref = raster(ls), field > 0.5
    union |= r
    print(name, 'loops', len(ls), 'points', sum(len(l) for l in ls), 'IoU vs own field %.3f' % ((r & ref).sum() / max((r | ref).sum(), 1)))
    d = ' '.join('M' + ' L'.join('%.1f %.1f' % p for p in l) + 'Z' for l in ls)
    body.append('<path class=`%s` fill=`%s` fill-rule=`evenodd` d=`%s`/>' % (name, FILL[name], d))
    points += [p for l in ls for p in l]
ref_all = alpha > 0.5
print('all layers IoU vs alpha>0.5: %.3f' % ((union & ref_all).sum() / (union | ref_all).sum()))
x0, y0 = int(math.floor(min(p[0] for p in points))), int(math.floor(min(p[1] for p in points)))
x1, y1 = int(math.ceil(max(p[0] for p in points))), int(math.ceil(max(p[1] for p in points)))
svg = '<svg xmlns=`http://www.w3.org/2000/svg` viewBox=`%d %d %d %d`>%s</svg>\n' % (x0, y0, x1 - x0, y1 - y0, ''.join(body))
(OUT / 'logo-wide.svg').write_text(svg.replace('`', chr(34)), encoding='utf-8', newline='\n')
print('Wrote logo-wide.svg', len(svg), 'bytes, viewBox', (x0, y0, x1 - x0, y1 - y0))

# 同时把矢量写进 ui/index.html 的图标精灵（<symbol id="i-logo">）；颜色不在这里定，由 app.css 的 .logo-k/.logo-a/.logo-c 读主题令牌
import re
paths = re.findall(r'class="(\w)" fill="#\w+" fill-rule="evenodd" d="([^"]+)"', svg.replace('`', '"'))
symbol = ('<!--KPL_LOGO_START--><symbol id="i-logo" viewBox="%d %d %d %d">' % (x0, y0, x1 - x0, y1 - y0)
          + ''.join('<path class="logo-%s" fill-rule="evenodd" d="%s"/>' % p for p in paths) + '</symbol><!--KPL_LOGO_END-->')
html_path = OUT.parent / 'ui' / 'index.html'
html = html_path.read_text(encoding='utf-8', newline='')
if '<!--KPL_LOGO_START-->' in html:
    a, b = html.index('<!--KPL_LOGO_START-->'), html.index('<!--KPL_LOGO_END-->') + len('<!--KPL_LOGO_END-->')
    html = html[:a] + symbol + html[b:]
else:
    html, n = re.subn(r'(</svg>)(\s*<div class="app" id="app">)', lambda m: symbol + m.group(1) + m.group(2), html, count=1)
    if n != 1: raise SystemExit('index.html 里找不到图标精灵的结尾')
html_path.write_text(html, encoding='utf-8', newline='')
print('Updated', html_path)
