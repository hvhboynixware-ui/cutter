# -*- coding: utf-8 -*-
"""Собирает icon.ico для приложения: янтарный знак с двумя полосами (как в интерфейсе)."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "window" / "icon.ico"

AMBER_TOP = (255, 194, 92)
AMBER_BOTTOM = (224, 125, 12)
INK = (16, 12, 6)


def rounded(size: int) -> Image.Image:
    scale = 8  # рисуем крупно и уменьшаем — так края гладкие
    big = size * scale
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    radius = int(big * 0.26)
    # вертикальный градиент по маске
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=radius, fill=255)

    gradient = Image.new("RGB", (1, big))
    for y in range(big):
        t = y / max(1, big - 1)
        gradient.putpixel((0, y), (
            int(AMBER_TOP[0] * (1 - t) + AMBER_BOTTOM[0] * t),
            int(AMBER_TOP[1] * (1 - t) + AMBER_BOTTOM[1] * t),
            int(AMBER_TOP[2] * (1 - t) + AMBER_BOTTOM[2] * t),
        ))
    gradient = gradient.resize((big, big))

    plate = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    plate.paste(gradient, (0, 0), mask)
    canvas = Image.alpha_composite(canvas, plate)

    draw = ImageDraw.Draw(canvas)
    bar_w = int(big * 0.13)
    bar_top = int(big * 0.28)
    bar_bottom = int(big * 0.72)
    gap = int(big * 0.10)
    center = big // 2
    for offset in (-1, 1):
        x0 = center + offset * gap - bar_w // 2 if offset < 0 else center + gap - bar_w // 2
        x0 = center + (offset * gap) - bar_w // 2
        x1 = x0 + bar_w
        draw.rounded_rectangle([x0, bar_top, x1, bar_bottom], radius=bar_w // 2, fill=INK)

    return canvas.resize((size, size), Image.LANCZOS)


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [rounded(size) for size in sizes]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f"icon: {OUT} -> {(OUT.stat().st_size / 1024):.1f} KB, sizes {sizes}")


if __name__ == "__main__":
    main()
