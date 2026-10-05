"""生成 app 图标 icon.ico：蓝色圆角底 + 两个交叠的对话气泡（两台电脑在对话）。

图形坐标按 256 格设计，与 index.html 里的 SVG favicon 一致；先画 4 倍大再缩小以获得抗锯齿。
仅打包前需要运行（依赖 Pillow）：.venv\\Scripts\\python.exe make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 4  # 超采样倍数
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
TOP, BOTTOM = (0x4D, 0x8B, 0xFF), (0x2A, 0x5B, 0xF0)
BLUE = (0x33, 0x70, 0xFF, 255)


def p(*xy):
    return [v * S for v in xy]


def draw():
    size = 256 * S
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 圆角底板，自上而下渐变
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(p(8, 8, 248, 248), radius=52 * S, fill=255)
    img.paste(grad.resize((size, size)), (0, 0), mask)

    # 后面的半透明气泡（左上，尾巴朝左下）
    back = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(back)
    d.rounded_rectangle(p(44, 54, 168, 140), radius=30 * S, fill=(255, 255, 255, 140))
    d.polygon(p(64, 128, 56, 166, 100, 136), fill=(255, 255, 255, 140))
    img = Image.alpha_composite(img, back)

    # 前面的白色气泡（右下，尾巴朝右下），里面两行"文字"
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(p(88, 102, 212, 188), radius=30 * S, fill="white")
    d.polygon(p(192, 176, 202, 212, 156, 186), fill="white")
    for x1, y in ((178, 134), (154, 160)):
        d.line(p(118, y, x1, y), fill=BLUE, width=13 * S)
        for x in (118, x1):  # 圆头线帽
            r = 6.5 * S
            d.ellipse((x * S - r, y * S - r, x * S + r, y * S + r), fill=BLUE)
    return img


if __name__ == "__main__":
    out = Path(__file__).with_name("icon.ico")
    big = draw().resize((256, 256), Image.LANCZOS)
    big.save(out, sizes=[(s, s) for s in SIZES])
    print(f"已生成 {out}")
