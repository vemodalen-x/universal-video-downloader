# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets\\app_icon_v2.ico', 'assets'), ('assets\\app_brand_v2_40.png', 'assets'), ('assets\\app_icon_v2_64.png', 'assets')]
binaries = []
hiddenimports = []
EXCLUDED_DATA_SUFFIXES = ('.py', '.pyc', '.dist-info/delvewheel')
# The modules are bundled through hiddenimports/PYZ. Shipping upstream .py/.pyc
# data would expose extractor fixtures and API test constants without helping runtime.
# DELVEWHEEL records third-party wheel build paths and is not required at runtime.
for package in ('yt_dlp', 'curl_cffi'):
    tmp_ret = collect_all(package)
    datas += [
        entry
        for entry in tmp_ret[0]
        if not entry[0].replace('\\', '/').lower().endswith(EXCLUDED_DATA_SUFFIXES)
    ]
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
# PyInstaller auto-collects distribution metadata after the package data above.
# DELVEWHEEL contains third-party CI paths and has no runtime purpose.
a.datas = [
    entry
    for entry in a.datas
    if not entry[0].replace('\\', '/').lower().endswith('.dist-info/delvewheel')
]
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
