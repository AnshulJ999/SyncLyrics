# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['sync_lyrics.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('.env.example', '.'),
        # Note: certs folder is generated at runtime when HTTPS is enabled
    ],
    hiddenimports=[
        # === Windows SDK & System Tray ===
        'winsdk',
        'pystray',
        'PIL',
        'PIL.Image',
        
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
        
        # === Audio Recognition Engine (NEW) ===
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
        # scipy removed - using numpy fallback for audio resampling (saves ~100MB in build)
        'psutil',
        
        # === Audio Recognition Custom Modules ===
        'audio_recognition',
        'audio_recognition.capture',
        'audio_recognition.shazam',
        'audio_recognition.engine',
        'audio_recognition.buffer',
        'audio_recognition.acrcloud',
        
        # === System Utils Package (Refactored) ===
        'system_utils',
        'system_utils.state',
        'system_utils.helpers',
        'system_utils.image',
        'system_utils.album_art',
        'system_utils.artist_image',
        'system_utils.metadata',
        'system_utils.windows',
        'system_utils.spotify',
        'system_utils.reaper',
        'system_utils.session_config',
        'system_utils.spicetify',
        'system_utils.spicetify_db',
        
        # === Media Sources (plugin system) ===
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
        
        # === Utilities ===
        'benedict',
        'desktop_notifier',
        'desktop_notifier.winrt',
        'colorama',
        'yaml',
        'urllib3',
        'dotenv',
        
        # === Windows APIs ===
        'win32api',
        'win32con',
        'ctypes',
        
        # === Standard Library (sometimes missed) ===
        'wave',
        'io',
        'dataclasses',
        'enum',
        'asyncio',
        'concurrent.futures',
        'threading',
        'faulthandler',
        'argparse',
    ],
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
