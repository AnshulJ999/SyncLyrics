# -*- mode: python ; coding: utf-8 -*-
# Windows build spec - uses auto-discovery for internal packages

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# === Auto-collect internal packages (new files automatically included) ===
internal_packages = (
    collect_submodules('system_utils') +
    collect_submodules('providers') +
    collect_submodules('audio_recognition')
)

# === External packages that need explicit hints ===
external_hints = [
    # Windows SDK & System Tray
    'winsdk',
    'pystray',
    'PIL',
    'PIL.Image',
    
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
    'psutil',
    
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
    
    # Utilities
    'benedict',
    'desktop_notifier',
    'desktop_notifier.winrt',
    'colorama',
    'yaml',
    'urllib3',
    'dotenv',
    
    # Windows APIs
    'win32api',
    'win32con',
    'ctypes',
    
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
]

a = Analysis(
    ['sync_lyrics.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('.env.example', '.'),
    ],
    hiddenimports=internal_packages + external_hints,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'scipy',      # Optional pydub dependency - not needed (ENABLE_RESAMPLING=False)
        'matplotlib', # Transitive scipy dependency - not used
        'tkinter',    # GUI toolkit - not used (saves ~10MB)
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
    name='SyncLyrics',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Set to True to show console (for debugging)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/images/icon.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SyncLyrics',
)
