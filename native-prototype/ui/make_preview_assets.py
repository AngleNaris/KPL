"""生成浏览器预览用的合成占位图（不是项目素材）。用法：python -I make_preview_assets.py"""
from pathlib import Path
import colorsys
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


def main():
    OUT.mkdir(exist_ok=True)
    for index, name in enumerate(ASSETS, 1):
        tile(OUT / f'{index:02d}_{name}_idA{index:02d}_v001_draft.png', (400, 300),
             f'A{index:02d} {name}', '合成占位 · 非项目素材', index / len(ASSETS))
    for index in range(1, 9):
        tile(OUT / f'12_参考画面_idS{index:02d}_v001_draft.png', (480, 270),
             f'S{index:02d}', '合成占位画面', 0.08 + index / 20)


if __name__ == '__main__':
    main()
