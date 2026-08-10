# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the Lazybones portable release."""

from PyInstaller.utils.hooks import collect_all


datas = [
    ("prompts/v1.0_basic.yaml", "prompts"),
    ("Readme.yaml", "."),
    ("icon.ico", "."),
    ("dist-tools/LazybonesUpdater.exe", "."),
]
binaries = []
hiddenimports = [
    "fitz",
    "pdfplumber",
    "docx",
    "openpyxl",
    "yaml",
    "win32com.client",
    "pythoncom",
    "pywintypes",
]

for package in ("openai", "tkinterdnd2", "rapidocr", "onnxruntime"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    ["src/main.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["hooks"],
    runtime_hooks=[],
    excludes=["pytest", "IPython", "notebook"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lazybones",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="icon.ico",
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Lazybones",
)
