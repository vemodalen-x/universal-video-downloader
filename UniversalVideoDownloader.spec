# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets\\app_icon_v2.ico', 'assets'), ('assets\\app_brand_v2_40.png', 'assets'), ('assets\\app_icon_v2_64.png', 'assets')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('yt_dlp')
# The modules are bundled through hiddenimports/PYZ. Shipping upstream .py/.pyc
# data would expose extractor fixtures and API test constants without helping runtime.
datas += [entry for entry in tmp_ret[0] if not entry[0].lower().endswith(('.py', '.pyc'))]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    ['m3u8_desktop_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='UniversalVideoDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='assets\\version_info.txt',
    icon=['assets\\app_icon_v2.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='UniversalVideoDownloader',
)
