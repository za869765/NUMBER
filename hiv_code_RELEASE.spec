# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — HIV 取號 RELEASE（正式版）
# 編譯指令：pyinstaller hiv_code_RELEASE.spec --noconfirm
# 與 DEBUG 版差異：console=False（隱藏黑窗）+ 檔名無 _DEBUG 後綴

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

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
# v1.0.39 雲端授權只用 stdlib urllib + ssl，不需 hidden import

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

def _read_version():
    try:
        with open('VERSION.txt', 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return '0.0.0'

VER = _read_version()
EXE_NAME = f'HIV取號_v{VER}'   # 正式版：無 _DEBUG 後綴

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
    console=False,   # 正式版：隱藏 console 黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
