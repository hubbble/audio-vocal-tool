#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""產生 app.ico：圓角漸層底 + 白色音波長條，給 exe 當圖示用。"""

from PIL import Image, ImageDraw

SIZE = 256            # 主圖大小（之後縮成多尺寸 ico）
BG_TOP = (124, 92, 255)    # 紫
BG_BOTTOM = (56, 189, 248)  # 藍
RADIUS = 52


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def gradient(size, top, bottom):
    base = Image.new("RGB", (size, size), top)
    top_img = Image.new("RGB", (size, size), bottom)
    mask = Image.new("L", (size, size))
    mask.putdata([int(255 * (y / size)) for y in range(size) for _ in range(size)])
    base.paste(top_img, (0, 0), mask)
    return base


def main():
    img = gradient(SIZE, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # 中央音波長條（高低不一，像音訊波形）
    heights = [0.30, 0.55, 0.80, 1.0, 0.65, 0.90, 0.45, 0.70, 0.35]
    n = len(heights)
    bar_w = 14
    gap = (SIZE - n * bar_w) / (n + 1)
    cx_mid = SIZE / 2
    for i, h in enumerate(heights):
        x0 = gap + i * (bar_w + gap)
        x1 = x0 + bar_w
        bar_h = h * (SIZE * 0.5)
        y0 = cx_mid - bar_h / 2
        y1 = cx_mid + bar_h / 2
        draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_w / 2,
                               fill=(255, 255, 255, 235))

    # 套圓角
    img.putalpha(rounded_mask(SIZE, RADIUS))

    # 輸出多尺寸 ico
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save("app.ico", format="ICO", sizes=sizes)
    print("已產生 app.ico")


if __name__ == "__main__":
    main()
