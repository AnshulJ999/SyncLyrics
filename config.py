"""
SyncLyrics Configuration Loader
Loads values from settings.json via the settings manager.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Import the settings manager instance which holds the loaded JSON values
# We use a try-except block to handle circular imports if any,
# though settings.py should be independent.
try:
    from settings import settings
except ImportError:
    # Fallback if something goes wrong during boot
    class MockSettings:
        def get(self, k): return None
    settings = MockSettings()

# ==========================================
# Path Configuration
# ==========================================
if "__compiled__" in globals() or getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).parent

# ==========================================
# Version
# ==========================================
VERSION = "1.6.0"

# FIX: Only load .env if it exists (optimization)
env_file = ROOT_DIR / '.env'
if env_file.exists():
    load_dotenv(env_file)

# Helper to prefer Env Var > Settings JSON > Default
def conf(key, default=None):
    # 1. Check Env Var (Highest Priority - good for docker/dev)
    env_val = os.getenv(key.upper().replace('.', '_'))
    if env_val is not None:
        return env_val
    
    # 2. Check Settings JSON
    json_val = settings.get(key)
    if json_val is not None:
        return json_val
        
    # 3. Default
    return default

# Type conversion helpers for environment variables
# (env vars are always strings, but config values may need to be int/float/bool)
def _safe_float(val, default: float) -> float:
    """Safely convert to float, returning default on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _safe_int(val, default):
    """Safely convert to int, returning default on failure. Supports None default."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

def _safe_bool(val, default: bool) -> bool:
    """Safely convert to bool, handling string 'true'/'false'."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ('true', '1', 'yes', 'on')
    return bool(val)

# ==========================================
# EXPORTED CONFIG DICTS
# ==========================================

RESOURCES_DIR = ROOT_DIR / "resources"

# Data directories - can be overridden via environment variables for persistent storage
# In HAOS, set these to /config/synclyrics/* for persistence across addon restarts
DATABASE_DIR = Path(os.getenv("SYNCLYRICS_LYRICS_DB", str(ROOT_DIR / "lyrics_database")))
CACHE_DIR = Path(os.getenv("SYNCLYRICS_CACHE_DIR", str(ROOT_DIR / "cache")))
ALBUM_ART_DB_DIR = Path(os.getenv("SYNCLYRICS_ALBUM_ART_DB", str(ROOT_DIR / "album_art_database")))
SPICETIFY_DB_DIR = Path(os.getenv("SYNCLYRICS_SPICETIFY_DB", str(ROOT_DIR / "spicetify_database")))
CERTS_DIR = Path(os.getenv("SYNCLYRICS_CERTS_DIR", str(ROOT_DIR / "certs")))

# FIX: Wrap directory creation in try-except for permission errors
for d in [RESOURCES_DIR, DATABASE_DIR, CACHE_DIR, ALBUM_ART_DB_DIR, SPICETIFY_DB_DIR, CERTS_DIR]:
    try:
        d.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        # Can't use logger here (not configured yet), so use print
        print(f"Warning: Failed to create directory {d}: {e}")

DEBUG = {
    "enabled": _safe_bool(conf("debug.enabled"), False),
    "log_file": conf("debug.log_file", "synclyrics.log"),
    # FIX: Default to INFO for all builds
    "log_level": conf("debug.log_level", "INFO"),
    "log_providers": _safe_bool(conf("debug.log_providers"), True),
    "log_polling": _safe_bool(conf("debug.log_polling"), True),
    # FIX: Default False for frozen EXE (no console window)
    "log_to_console": _safe_bool(conf("debug.log_to_console"), not getattr(sys, 'frozen', False)),
    "log_detailed": _safe_bool(conf("debug.log_detailed"), False),
    "performance_logging": _safe_bool(conf("debug.performance_logging"), False),
    "log_rotation": {
        "max_bytes": _safe_int(conf("debug.log_rotation.max_bytes"), 10485760),
        "backup_count": _safe_int(conf("debug.log_rotation.backup_count"), 10)
    }
}

import secrets

SERVER = {
    "port": _safe_int(conf("server.port"), 9012),
    "host": conf("server.host", "0.0.0.0"),
    # FIX: Generate secure random key if not provided (required for session security)
    "secret_key": os.getenv("QUART_SECRET_KEY") or secrets.token_hex(32),
    "debug": _safe_bool(conf("server.debug"), False),
    "https": {
        "enabled": _safe_bool(conf("server.https.enabled"), True),
        "port": _safe_int(conf("server.https.port"), 9013),  # 0 = same as HTTP, >0 = dual-stack
        "auto_generate": _safe_bool(conf("server.https.auto_generate"), True),
        "cert_file": conf("server.https.cert_file", "certs/server.crt"),
        "key_file": conf("server.https.key_file", "certs/server.key"),
    },
}

UI = {
    "themes": {
        "default": {
            "bg_start": conf("ui.themes.default.bg_start", "#24273a"),
            "bg_end": conf("ui.themes.default.bg_end", "#363b54"),
            "text": conf("ui.themes.default.text", "#ffffff")
        },
        "dark": {
            "bg_start": conf("ui.themes.dark.bg_start", "#1c1c1c"),
            "bg_end": conf("ui.themes.dark.bg_end", "#2c2c2c"),
            "text": conf("ui.themes.dark.text", "#ffffff")
        },
        "light": {
            "bg_start": conf("ui.themes.light.bg_start", "#ffffff"),
            "bg_end": conf("ui.themes.light.bg_end", "#f0f0f0"),
            "text": conf("ui.themes.light.text", "#000000")
        }
    },
    "animation_styles": conf("ui.animation_styles", ["wave", "fade", "slide", "none"]),
    "background_styles": conf("ui.background_styles", ["gradient", "solid", "albumart"]),
    "minimal_mode": {
        "enabled": _safe_bool(conf("ui.minimal_mode.enabled"), False),
        "hide_elements": conf("ui.minimal_mode.hide_elements", ["bottom-nav"])
    }
}

LYRICS = {
    "display": {
        "buffer_size": _safe_int(conf("lyrics.display.buffer_size"), 6),
        "update_interval": _safe_float(conf("lyrics.display.update_interval"), 0.1),
        "idle_interval": _safe_float(conf("lyrics.display.idle_interval"), 2.0),
        "latency_compensation": _safe_float(conf("lyrics.display.latency_compensation"), -0.1),
        "spotify_latency_compensation": _safe_float(conf("lyrics.display.spotify_latency_compensation"), -0.5),
        "audio_recognition_latency_compensation": _safe_float(conf("lyrics.display.audio_recognition_latency_compensation"), 0.1),
        "spicetify_latency_compensation": _safe_float(conf("lyrics.display.spicetify_latency_compensation"), 0.0),
        "word_sync_latency_compensation": _safe_float(conf("lyrics.display.word_sync_latency_compensation"), -0.1),
        "musixmatch_word_sync_offset": _safe_float(conf("lyrics.display.musixmatch_word_sync_offset"), -0.1),
        "netease_word_sync_offset": _safe_float(conf("lyrics.display.netease_word_sync_offset"), -0.1),
        "idle_wait_time": _safe_float(conf("lyrics.display.idle_wait_time"), 10.0),
        "smart_race_timeout": _safe_float(conf("lyrics.display.smart_race_timeout"), 4.0),
    },
}

SPOTIFY = {
    # FIX: Use empty string instead of None for null safety with spotipy
    "client_id": os.getenv("SPOTIFY_CLIENT_ID", ""),
    "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET", ""),
    "redirect_uri": conf("spotify.redirect_uri", "http://127.0.0.1:9012/callback"),
    "scope": [
        "user-read-playback-state", 
        "user-modify-playback-state", 
        "user-read-currently-playing",
        "user-library-read",    # ADDED: Check if song is liked
        "user-library-modify"   # ADDED: Like/Unlike songs
    ],
    "cache": {
        "metadata_ttl": _safe_float(conf("spotify.cache.metadata_ttl"), 2.0),
        "enabled": _safe_bool(conf("spotify.cache.enabled"), True),
    },
    # Polling intervals for Spotify API (configurable for Home Assistant)
    "polling": {
        # Fast mode: Used when Spotify is the only source (no Windows Media)
        "fast_interval": _safe_float(conf("spotify.polling.fast_interval"), 2.0),
        # Slow mode: Used in hybrid mode (with Windows Media) and when paused
        "slow_interval": _safe_float(conf("spotify.polling.slow_interval"), 6.0),
    }
}


PROVIDERS = {
    "lrclib": {
        "enabled": _safe_bool(conf("providers.lrclib.enabled"), True),
        "priority": _safe_int(conf("providers.lrclib.priority"), 2),
        "base_url": "https://lrclib.net/api",
        "timeout": _safe_int(conf("providers.lrclib.timeout"), 10),
        "retries": _safe_int(conf("providers.lrclib.retries"), 3),
        "cache_duration": _safe_int(conf("providers.lrclib.cache_duration"), 86400)
    },
    "spotify": {
        "enabled": _safe_bool(conf("providers.spotify.enabled"), True),
        "priority": _safe_int(conf("providers.spotify.priority"), 1),
        "base_url": os.getenv("SPOTIFY_BASE_URL", "https://fake-spotify-lyrics-api-azure.vercel.app"),
        "timeout": _safe_int(conf("providers.spotify.timeout"), 10),
        "retries": _safe_int(conf("providers.spotify.retries"), 3),
        "cache_duration": _safe_int(conf("providers.spotify.cache_duration"), 3600)
    },
    "qq": {
        "enabled": _safe_bool(conf("providers.qq.enabled"), True),
        "priority": _safe_int(conf("providers.qq.priority"), 5),
        "timeout": _safe_int(conf("providers.qq.timeout"), 10),
        "retries": _safe_int(conf("providers.qq.retries"), 3),
        "cache_duration": _safe_int(conf("providers.qq.cache_duration"), 86400)
    },
    "netease": {
        "enabled": _safe_bool(conf("providers.netease.enabled"), True),
        "priority": _safe_int(conf("providers.netease.priority"), 4),
        "timeout": _safe_int(conf("providers.netease.timeout"), 10),
        "retries": _safe_int(conf("providers.netease.retries"), 3),
        "cache_duration": _safe_int(conf("providers.netease.cache_duration"), 86400)
    },
    "musixmatch": {
        "enabled": _safe_bool(conf("providers.musixmatch.enabled"), True),
        "priority": _safe_int(conf("providers.musixmatch.priority"), 3),
        "timeout": _safe_int(conf("providers.musixmatch.timeout"), 15),
        "retries": _safe_int(conf("providers.musixmatch.retries"), 3),
        "cache_duration": _safe_int(conf("providers.musixmatch.cache_duration"), 86400)
    }
}

STORAGE = {
    "lyrics_db": {
        "enabled": _safe_bool(conf("storage.lyrics_db.enabled"), True),
        "max_size_mb": _safe_int(conf("storage.lyrics_db.max_size_mb"), 100),
        "cleanup_threshold": _safe_float(conf("storage.lyrics_db.cleanup_threshold"), 0.9),
        "file_pattern": conf("storage.lyrics_db.file_pattern", "*.json")
    },
    "cache": {
        "enabled": _safe_bool(conf("storage.cache.enabled"), True),
        "duration_days": _safe_int(conf("storage.cache.duration_days"), 30),
        "max_size_mb": _safe_int(conf("storage.cache.max_size_mb"), 50),
        "memory_items": _safe_int(conf("storage.cache.memory_items"), 100)
    }
}

NOTIFICATIONS = {
    "enabled": _safe_bool(conf("notifications.enabled"), True),
    "duration": _safe_int(conf("notifications.duration"), 3),
    "icon_path": conf("notifications.icon_path", str(RESOURCES_DIR / "images" / "icon.ico"))
}

MEDIA_SOURCE = {
    "sources": [
        {
            "name": "spicetify",
            "enabled": _safe_bool(conf("media_source.spicetify.enabled"), True),
            "priority": _safe_int(conf("media_source.spicetify.priority"), 0),
        },
        {
            "name": "spotify",
            "enabled": _safe_bool(conf("media_source.spotify.enabled"), True),
            "priority": _safe_int(conf("media_source.spotify.priority"), 2),
        },
        {
            "name": "windows_media",
            "enabled": _safe_bool(conf("media_source.windows_media.enabled"), True),
            "priority": _safe_int(conf("media_source.windows_media.priority"), 1),
        },
        {
            "name": "gnome",
            "enabled": _safe_bool(conf("media_source.gnome.enabled"), False),
            "priority": _safe_int(conf("media_source.gnome.priority"), 2),
        }
    ]
}

SYSTEM = {
    "windows": {
        "media_session": {
            "enabled": _safe_bool(conf("system.windows.media_session.enabled"), True),
            "preferred": _safe_bool(conf("system.windows.media_session.preferred"), True),
            "timeout": _safe_int(conf("system.windows.media_session.timeout"), 5)
        },
        "paused_timeout": _safe_int(conf("system.windows.paused_timeout"), 600),  # 10 min default
    },
    "spotify": {
        "paused_timeout": _safe_int(conf("system.spotify.paused_timeout"), 600),  # 10 min default
    },
    "spicetify": {
        "paused_timeout": _safe_int(conf("system.spicetify.paused_timeout"), 600),  # 10 min default
    },
    "linux": {
        "gsettings_enabled": _safe_bool(conf("system.linux.gsettings_enabled"), True),
        "playerctl_required": _safe_bool(conf("system.linux.playerctl_required"), True)
    }
}

FEATURES = {
    "minimal_ui": _safe_bool(conf("features.minimal_ui"), False),
    "save_lyrics_locally": _safe_bool(conf("features.save_lyrics_locally"), True),
    "show_lyrics_source": _safe_bool(conf("features.show_lyrics_source"), True),
    "parallel_provider_fetch": _safe_bool(conf("features.parallel_provider_fetch"), True),
    "provider_stats": _safe_bool(conf("features.provider_stats"), False),
    "auto_theme": _safe_bool(conf("features.auto_theme"), True),
    "album_art_colors": _safe_bool(conf("features.album_art_colors"), True),
    "album_art_db": _safe_bool(conf("features.album_art_db"), True),
    "word_sync_auto_switch": _safe_bool(conf("features.word_sync_auto_switch"), False),  # Respect provider priority
    "word_sync_default_enabled": _safe_bool(conf("features.word_sync_default_enabled"), True),  # Word-sync ON by default
    "spicetify_database": _safe_bool(conf("features.spicetify_database"), True),  # Cache audio analysis from Spicetify
}

ALBUM_ART = {
    "timeout": _safe_int(conf("album_art.timeout"), 5),
    "retries": _safe_int(conf("album_art.retries"), 2),
    # Note: lastfm_api_key is NOT in config - it's only read from environment variable
    # for security (should be in .env file, not settings.json)
    "enable_itunes": _safe_bool(conf("album_art.enable_itunes"), True),
    "enable_lastfm": _safe_bool(conf("album_art.enable_lastfm"), True),
    # Default to True since enhancement is proven to work and always falls back to 640px if unavailable
    "enable_spotify_enhanced": _safe_bool(conf("album_art.enable_spotify_enhanced"), True),
    "min_resolution": _safe_int(conf("album_art.min_resolution"), 3000)  # Prefer 3000x3000px for best quality
}

ARTIST_IMAGE = {
    "timeout": _safe_int(conf("artist_image.timeout"), 5),
    # Enable Wikipedia/Wikimedia integration (provides 1500-5000px high-res images)
    "enable_wikipedia": _safe_bool(conf("artist_image.enable_wikipedia"), False),
    # Enable FanArt.tv album covers (fetches album artwork, can be disabled if too many duplicates)
    "enable_fanart_albumcover": _safe_bool(conf("artist_image.enable_fanart_albumcover"), True)
}

# Audio Recognition (Reaper Integration)
# Uses ShazamIO for song identification with latency-compensated position tracking
AUDIO_RECOGNITION = {
    "enabled": _safe_bool(conf("audio_recognition.enabled"), False),
    # ENV override: REAPER_AUTO_DETECT=true in .env takes priority over settings.json
    "reaper_auto_detect": os.getenv("REAPER_AUTO_DETECT", "").lower() == "true" or _safe_bool(conf("audio_recognition.reaper_auto_detect"), False),
    "device_id": _safe_int(conf("audio_recognition.device_id"), None),  # None = auto-detect
    "device_name": conf("audio_recognition.device_name", ""),
    "capture_duration": _safe_float(conf("audio_recognition.capture_duration"), 6.0),
    "recognition_interval": _safe_float(conf("audio_recognition.recognition_interval"), 4.0),
    "latency_offset": _safe_float(conf("audio_recognition.latency_offset"), 0.0),
    "silence_threshold": _safe_int(conf("audio_recognition.silence_threshold"), 350),
    # Verification settings (anti-false-positive)
    "verification_cycles": _safe_int(conf("audio_recognition.verification_cycles"), 2),
    "verification_timeout_cycles": _safe_int(conf("audio_recognition.verification_timeout_cycles"), 4),
    "reaper_validation_enabled": _safe_bool(conf("audio_recognition.reaper_validation_enabled"), False),
    "reaper_validation_threshold": _safe_int(conf("audio_recognition.reaper_validation_threshold"), 80),
}

# Helper functions
def get_provider_config(name: str) -> dict:
    return PROVIDERS.get(name, {"enabled": False, "priority": 0})

def is_provider_enabled(name: str) -> bool:
    return PROVIDERS.get(name, {}).get("enabled", False)

def get_provider_priority(name: str) -> int:
    return PROVIDERS.get(name, {}).get("priority", 0)