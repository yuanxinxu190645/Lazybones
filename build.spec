# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# 收齐 openai 和 tkinterdnd2 的所有依赖
openai_datas, openai_binaries, openai_hiddenimports = collect_all('openai')
tk_datas, tk_binaries, tk_hiddenimports = collect_all('tkinterdnd2')

# ★ 关键:不打包 prompts 文件夹!
# 通用提示词 v1.0_basic.yaml 由 Inno Setup 装到 {app}\prompts\
# 你的私有调试提示词留在源码目录,不会进 EXE
# 同理:project / config 由程序运行时在 %APPDATA% 自动创建,这里不需要
datas = [
    ('Readme.yaml', '.'),
    ('icon.ico', '.'),
]

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=openai_binaries + tk_binaries,
    datas=datas + openai_datas + tk_datas,
    hiddenimports=[
        'fitz',
        'pdfplumber',
        'docx',
        'openpyxl',
        'yaml',
    ] + openai_hiddenimports + tk_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Lazybones',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # 先关掉,避免杀软误报
    runtime_tmpdir=None,
    console=False,
    icon='icon.ico',      # ← 只写一次!
)
