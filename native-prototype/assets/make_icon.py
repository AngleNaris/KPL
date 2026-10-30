# 由设计原图 logo-square-src.jpg 生成程序图标 kpl.ico（直角方形底，不用圆角；多尺寸）与检查用的 kpl-preview.png。
# 应用内的横版标识是矢量，见 trace_logo.py。需要 Pillow；用法：python -I make_icon.py
from pathlib import Path
from PIL import Image

OUT = Path(__file__).resolve().parent
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    square = Image.open(OUT / 'logo-square-src.jpg').convert('RGBA')
    square.save(OUT / 'kpl.ico', sizes=SIZES)
    # 预览：256 大图加 128/48/32/16 像素实际尺寸，用来检查小尺寸可读性
    sheet = Image.new('RGBA', (400, 256), (60, 60, 66, 255))
    for size, pos in ((256, (0, 0)), (128, (280, 0)), (48, (280, 150)), (32, (340, 150)), (16, (390, 150))):
        small = square.resize((size, size), Image.LANCZOS)
        sheet.paste(small, pos, small)
    sheet.save(OUT / 'kpl-preview.png')
    print('Wrote', OUT / 'kpl.ico')


if __name__ == '__main__':
    main()
