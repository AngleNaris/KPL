"""生成浏览器预览用的合成占位图与测试视频（不是项目素材）。用法：python -I make_preview_assets.py"""
from pathlib import Path
import colorsys
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / 'preview-assets'
FONT = 'C:/Windows/Fonts/msyhbd.ttc'
ASSETS = ['店面', '街景', '厨师', '锅具套件', '灶台', '筷子', '面条', '顾客', '店门', '瓷碗', '店内场景']


def tile(path, size, title, subtitle, hue):
    w, h = size
    r, g, b = (round(c * 255) for c in colorsys.hls_to_rgb(hue, 0.22, 0.18))
    img = Image.new('RGB', size, (r, g, b))
    draw = ImageDraw.Draw(img)
    line = (min(r + 24, 255), min(g + 24, 255), min(b + 24, 255))
    for x in range(-h, w, 24):
        draw.line([(x, h), (x + h, 0)], fill=line, width=1)
    big, small = ImageFont.truetype(FONT, h // 6), ImageFont.truetype(FONT, h // 14)
    draw.text((w / 2, h / 2 - h // 14), title, font=big, fill=(236, 234, 229), anchor='mm')
    draw.text((w / 2, h / 2 + h // 8), subtitle, font=small, fill=(181, 179, 173), anchor='mm')
    img.save(path)


def clip(path, seconds, hue):
    """合成测试视频（彩条 + 正弦音），需要已安装的 ffmpeg；没有时跳过。"""
    if not shutil.which('ffmpeg'):
        print('未找到 ffmpeg，跳过合成视频：' + path.name)
        return
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=640x360:rate=24:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency={220 + hue}:duration={seconds}', '-vf', f'hue=h={hue}',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)], check=True)


def main():
    OUT.mkdir(exist_ok=True)
    # 与 index.html 示例数据的主轨片段对应：S02 长于镜头、S04 短于镜头、S06 等长
    for name, seconds, hue in (('01_S02_视频_v001_draft.mp4', 4, 0), ('02_S04_视频_v001_draft.mp4', 1.5, 120),
                               ('03_S06_视频_v001_draft.mp4', 3, 240)):
        clip(OUT / name, seconds, hue)
    for index, name in enumerate(ASSETS, 1):
        tile(OUT / f'{index:02d}_{name}_idA{index:02d}_v001_draft.png', (400, 300),
             f'A{index:02d} {name}', '合成占位 · 非项目素材', index / len(ASSETS))
    for index in range(1, 9):
        tile(OUT / f'12_参考画面_idS{index:02d}_v001_draft.png', (480, 270),
             f'S{index:02d}', '合成占位画面', 0.08 + index / 20)


if __name__ == '__main__':
    main()
