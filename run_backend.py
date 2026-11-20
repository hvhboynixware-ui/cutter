# -*- coding: utf-8 -*-
"""Точка входа собранного бэкенда Cutter (PyInstaller)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app.main import main
    sys.exit(main() or 0)
