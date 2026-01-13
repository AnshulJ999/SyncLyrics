# -*- mode: python ; coding: utf-8 -*-
# macOS build spec - creates folder distribution (no .app bundle due to PyInstaller issues)

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# === Auto-collect internal packages (new files automatically included) ===
internal_packages = (
    collect_submodules('system_utils') +
    collect_submodules('providers') +
    collect_submodules('audio_recognition')
)

# === Collect all encodings (required for macOS) ===
encodings_imports = collect_submodules('encodings')

# === External packages that need explicit hints ===
external_hints = [
    # Web Framework (Quart/Hypercorn)
    'hypercorn.protocol.h2',
    'hypercorn.protocol.h11',
    'wsproto',
    'engineio.async_drivers.aiohttp',
    'quart',
    'werkzeug',
    'jinja2',
    'click',
    'blinker',
    'itsdangerous',
    
    # Audio libraries
    'shazamio',
    'shazamio.api',
    'shazamio.factory',
    'shazamio.signature',
    'shazamio.algorithm',
    'shazamio.misc',
    'shazamio.models',
    'shazamio.enums',
    'shazamio.exceptions',
    'sounddevice',
    'numpy',
    'numpy.core',
    'numpy.core._multiarray_umath',
    'numpy.linalg',
    'numpy.fft',
    
    # Network & APIs
    'zeroconf',
    'zeroconf._utils',
    'zeroconf._handlers',
    'zeroconf._services',
    'zeroconf.asyncio',
    'spotipy',
    'spotipy.oauth2',
    'spotipy.cache_handler',
    'aiohttp',
    
    # HTTPS/SSL Support
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.hashes',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.x509',
    
    # Image Processing
    'PIL',
    'PIL.Image',
    
    # Utilities
    'benedict',
    'colorama',
    'yaml',
    'urllib3',
    'dotenv',
    
    # Standard Library (sometimes missed)
    'wave',
    'io',
    'dataclasses',
    'enum',
    'asyncio',
    'concurrent.futures',
    'threading',
    'faulthandler',
    'argparse',
    'ctypes',
]

a = Analysis(
    ['sync_lyrics.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('.env.example', '.'),
    ],
    hiddenimports=internal_packages + external_hints + encodings_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only
        'winsdk',
        'pywin32',
        'win32api',
        'win32con',
        'pystray',
        'desktop_notifier',
        # Heavy optional deps
        'scipy',
        'matplotlib',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,  # Reverted - BUNDLE was the problem, not the archive
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SyncLyrics',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Command-line executable (no .app bundle)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/images/icon.icns',  # Use .icns for macOS
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SyncLyrics',
)

# NOTE: No BUNDLE - shipping as folder (like Linux) to avoid PyInstaller BUNDLE bugs

