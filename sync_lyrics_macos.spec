# -*- mode: python ; coding: utf-8 -*-
# macOS build spec - creates .app bundle

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Collect all encodings submodules dynamically
encodings_imports = collect_submodules('encodings')

a = Analysis(
    ['sync_lyrics.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('.env.example', '.'),
    ],
    hiddenimports=[
        # === Web Framework (Quart/Hypercorn) ===
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
        
        # === Audio Recognition ===
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
        
        # === Audio Recognition Custom Modules ===
        'audio_recognition',
        'audio_recognition.capture',
        'audio_recognition.shazam',
        'audio_recognition.engine',
        'audio_recognition.buffer',
        'audio_recognition.acrcloud',
        
        # === System Utils Package ===
        'system_utils',
        'system_utils.state',
        'system_utils.helpers',
        'system_utils.image',
        'system_utils.album_art',
        'system_utils.artist_image',
        'system_utils.metadata',
        'system_utils.reaper',
        'system_utils.session_config',
        'system_utils.spicetify',
        'system_utils.spicetify_db',
        'system_utils.spotify',
        
        # === Media Sources (cross-platform only) ===
        'system_utils.sources',
        'system_utils.sources.base',
        'system_utils.sources.enrichment',
        'system_utils.sources.music_assistant',
        
        # === Providers Package ===
        'providers',
        'providers.base',
        'providers.lrclib',
        'providers.netease',
        'providers.qq',
        'providers.musixmatch',
        'providers.spotify_api',
        'providers.spotify_lyrics',
        'providers.album_art',
        'providers.artist_image',
        
        # === Network & APIs ===
        'zeroconf',
        'zeroconf._utils',
        'zeroconf._handlers',
        'zeroconf._services',
        'zeroconf.asyncio',
        'spotipy',
        'spotipy.oauth2',
        'spotipy.cache_handler',
        'aiohttp',
        
        # === HTTPS/SSL Support ===
        'cryptography',
        'cryptography.hazmat',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.primitives.serialization',
        'cryptography.x509',
        
        # === Image Processing ===
        'PIL',
        'PIL.Image',
        
        # === Utilities ===
        'benedict',
        'colorama',
        'yaml',
        'urllib3',
        'dotenv',
        
        # === Standard Library ===
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
    ] + encodings_imports,  # Add all encodings modules dynamically
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
    console=False,  # Command-line executable (no .app bundle)
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
    upx=False,
    upx_exclude=[],
    name='SyncLyrics',
)

# NOTE: No BUNDLE - shipping as folder (like Linux) to avoid PyInstaller BUNDLE bugs

