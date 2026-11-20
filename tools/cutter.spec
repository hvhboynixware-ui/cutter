# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-сборка бэкенда Cutter (onedir)."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent  # tools/ -> корень репозитория
FFMPEG_DIR = Path(os.environ.get("CUTTER_FFMPEG_DIR", r"C:\Users\ogkysh\Tools\ffmpeg\bin"))

datas = [
    (str(ROOT / "app" / "web"), "app/web"),
    (str(ROOT / "app" / "window" / "icon.ico"), "app/window"),
]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")

binaries = []
for name in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = FFMPEG_DIR / name
    if candidate.is_file():
        binaries.append((str(candidate), "."))
    else:
        print(f"[spec] ВНИМАНИЕ: нет {candidate} — ffmpeg не попадёт в сборку")

binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("av")

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("faster_whisper")
    + collect_submodules("yt_dlp")
    + collect_submodules("playwright")
    + [
        "app.server",
        "app.jobs",
        "app.main",
        "app.core.pipeline",
        "app.core.llm",
        "app.core.youtube",
        "app.core.models",
        "app.core.upload",
        "app.core.upload.tiktok",
        "app.core.upload.youtube",
        "app.core.upload.vk",
        "app.core.upload.browsers",
        "app.core.upload.selectors",
        "email.mime.text",
        "email.mime.multipart",
        "multipart",
    ]
)

a = Analysis(
    [str(ROOT / "run_backend.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "pytest",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "notebook",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cutter_backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "app" / "window" / "icon.ico") if (ROOT / "app" / "window" / "icon.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Cutter",
)
