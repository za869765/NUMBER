# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — HIV 取號 DEBUG 版
# 編譯指令：pyinstaller hiv_code_DEBUG.spec --noconfirm

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# v1.0.28：列舉真正會用到的 selenium 子模組（之前用 collect_submodules 包山包海，啟動拖慢）
hidden_selenium = [
    'selenium',
    'selenium.webdriver',
    'selenium.webdriver.edge',
    'selenium.webdriver.edge.webdriver',
    'selenium.webdriver.edge.options',
    'selenium.webdriver.edge.service',
    'selenium.webdriver.edge.remote_connection',
    'selenium.webdriver.chromium',
    'selenium.webdriver.chromium.webdriver',
    'selenium.webdriver.chromium.options',
    'selenium.webdriver.chromium.service',
    'selenium.webdriver.chromium.remote_connection',
    'selenium.webdriver.common',
    'selenium.webdriver.common.by',
    'selenium.webdriver.common.keys',
    'selenium.webdriver.common.action_chains',
    'selenium.webdriver.common.utils',
    'selenium.webdriver.common.options',
    'selenium.webdriver.common.service',
    'selenium.webdriver.common.driver_finder',
    'selenium.webdriver.common.selenium_manager',
    'selenium.webdriver.support',
    'selenium.webdriver.support.ui',
    'selenium.webdriver.support.expected_conditions',
    'selenium.webdriver.support.wait',
    'selenium.webdriver.support.select',
    'selenium.webdriver.remote',
    'selenium.webdriver.remote.webdriver',
    'selenium.webdriver.remote.webelement',
    'selenium.webdriver.remote.command',
    'selenium.webdriver.remote.errorhandler',
    'selenium.webdriver.remote.remote_connection',
    'selenium.common',
    'selenium.common.exceptions',
]
hidden_others = ['openpyxl', 'openpyxl.styles', 'openpyxl.worksheet.datavalidation',
                 'openpyxl.comments',
                 'urllib3', 'trio', 'trio_websocket', 'outcome', 'sniffio',
                 'attrs', 'websocket', 'wsproto', 'h11', 'idna', 'certifi']

a = Analysis(
    ['hiv_code.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('selenium'),
    hiddenimports=hidden_selenium + hidden_others,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ─ 讀取 VERSION ─
def _read_version():
    try:
        with open('VERSION.txt', 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return '0.0.0'

VER = _read_version()
EXE_NAME = f'HIV取號_v{VER}_DEBUG'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # DEBUG 版：保留 console 視窗看 traceback
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ─ EXE 輸出位置 ─
# 本專案不使用本地 dist\ 資料夾，避免與舊版搞混。
# EXE 統一輸出到 D:\Backup\Desktop\CODE\number\，舊版搬至 number\old\
# 編譯請使用 build_debug.bat（已帶 --distpath D:\Backup\Desktop\CODE\number）
# 若直接 pyinstaller hiv_code_DEBUG.spec，會用 PyInstaller 預設 dist\ — 不建議
