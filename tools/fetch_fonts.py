# -*- coding: utf-8 -*-
"""Один раз качает шрифты с Google Fonts в app/web/assets/fonts и правит CSS на локальные пути.

Нужно, чтобы приложение выглядело одинаково и без интернета.
Файлы дедуплицируются по URL: вариативные шрифты отдают один файл на несколько весов.
"""

from __future__ import annotations

import hashlib
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "app" / "web" / "assets" / "fonts"
CSS_OUT = ROOT / "app" / "web" / "assets" / "fonts.css"

FAMILIES = [
    ("Unbounded", "https://fonts.googleapis.com/css2?family=Unbounded:wght@500;600;700&display=swap"),
    ("Manrope", "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap"),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def keep_subset(block: str) -> bool:
    if "unicode-range" not in block:
        return True
    text = block.lower()
    return "u+0000-00ff" in text or "u+0400-045f" in text or "u+0301" in text


def main() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    for old in FONTS_DIR.glob("*.woff2"):
        old.unlink()

    cache: dict[str, str] = {}
    chunks: list[str] = []
    for family, url in FAMILIES:
        css = fetch(url).decode("utf-8")
        blocks = re.findall(r"@font-face\s*\{.*?\}", css, flags=re.S)
        kept = [b for b in blocks if keep_subset(b)]
        print(f"{family}: {len(blocks)} блоков, беру {len(kept)}")
        for block in kept:
            match = re.search(r"url\((https://[^)]+\.woff2)\)", block)
            if not match:
                continue
            remote = match.group(1)
            if remote not in cache:
                digest = hashlib.sha1(remote.encode()).hexdigest()[:8]
                name = f"{family.lower()}-{digest}.woff2"
                (FONTS_DIR / name).write_bytes(fetch(remote))
                cache[remote] = name
            chunks.append(block.replace(remote, f"assets/fonts/{cache[remote]}"))

    CSS_OUT.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    size = sum((FONTS_DIR / n).stat().st_size for n in cache.values())
    print(f"готово: {len(cache)} уникальных файлов, {size / 1024:.0f} КБ -> {CSS_OUT}")


if __name__ == "__main__":
    main()
