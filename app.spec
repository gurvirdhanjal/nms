# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

project_root = os.path.abspath(".")

# Collect Flask-related dynamic imports
flask_hidden = collect_submodules("flask")
sqlalchemy_hidden = collect_submodules("sqlalchemy")

# Collect your internal app modules
internal_modules = []
for pkg in [
    "routes",
    "services",
    "models",
    "middleware",
    "utils",
    "events",
    "metrics",
    "thresholds",
    "file_transfer",
]:
    try:
        internal_modules += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    ['app.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('instance', 'instance')
    ],
    hiddenimports=[
        *flask_hidden,
        *sqlalchemy_hidden,
        'flask_login',
        'flask_sqlalchemy',
        'werkzeug.security',
        'jinja2',
        'click',
        'engineio.async_drivers.threading', 
        'dns', 
        'dns.resolver',
        'pysnmp.smi.mibs',
        'pysnmp.smi.mibs.instances',
        'pysnmp.entity.rfc3413.oneliner.cmdgen',
        'dotenv',
        'wmi',
        'psutil',
        'cv2',
        'numpy',
        'aioping',
        'mac_vendor_lookup',
        'paramiko',
        *internal_modules,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['venv', 'pytest', 'tests'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NMS_Dashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # GUI / Web app
)

