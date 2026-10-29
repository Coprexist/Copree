from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from PyInstaller.utils.hooks import collect_submodules
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['run_packaged.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('backend/app', 'app'),
        ('frontend/dist', 'frontend/dist'),
        ('frontend/public/logo-transparent.ico', 'frontend/public'),
    ] + collect_data_files('customtkinter')
      # tzdata 只有数据文件没有模块逻辑：Windows 上没有系统时区库，
      # zoneinfo 全靠它，漏了就是 ZoneInfoNotFoundError（私信触发回复时直接崩）
      + collect_data_files('tzdata'),
    hiddenimports=[
        'tzdata',
        'customtkinter',
        'customtkinter',
        'pystray',
        'pydantic_settings',
        'sqlalchemy',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.sqlite.aiosqlite',
        'aiosqlite',
        'greenlet',
        'fastapi.middleware.cors',
        'fastapi.staticfiles',
        'passlib.context',
        'passlib.hash.bcrypt',
        'passlib.hash.sha256_crypt',
        'jose',
        'jose.jwt',
        'jose.jws',
        'jose.jwe',
        'httpx',
        'PIL',
        'bcrypt',
        'python_multipart',
        'cryptography.fernet',
    ] + collect_submodules('passlib', filter=lambda name: not name.startswith('passlib.tests')) + collect_submodules('jose'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'mypy',
        'hypothesis',
        'IPython',
        'trio',
        'uvloop',
        'winloop',
        'psycopg2',
        'psycopg',
        'asyncpg',
        'pymysql',
        'mysql',
        'oracledb',
        'cx_Oracle',
        'gunicorn',
        'watchfiles',
        'wsproto',
        'werkzeug',
        'python_socks',
        'yaml',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Copree',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='frontend/public/logo-transparent.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Copree',
)
