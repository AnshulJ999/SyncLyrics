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
    ROOT_DIR = Path(sys.argv[0]).parent
else:
    ROOT_DIR = Path(__file__).parent

load_dotenv(ROOT_DIR / '.env')

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

# ==========================================
# EXPORTED CONFIG DICTS
# ==========================================

RESOURCES_DIR = ROOT_DIR / "resources"
DATABASE_DIR = ROOT_DIR / "lyrics_database"
CACHE_DIR = ROOT_DIR / "cache"

for d in [RESOURCES_DIR, DATABASE_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DEBUG = {
    "enabled": conf("debug.enabled", False),
    "log_file": conf("debug.log_file", "synclyrics.log"),
    "log_level": conf("debug.log_level", "WARNING"),
    "log_providers": conf("debug.log_providers", True),
    "log_polling": conf("debug.log_polling", True),
    "log_to_console": conf("debug.log_to_console", True),
    "log_detailed": conf("debug.log_detailed", False),
    "performance_logging": conf("debug.performance_logging", False),
    "log_rotation": {
        "max_bytes": conf("debug.log_rotation.max_bytes", 10485760),
        "backup_count": conf("debug.log_rotation.backup_count", 5)
    }
}

SERVER = {
    "port": conf("server.port", 9012),
    "host": conf("server.host", "0.0.0.0"),
    "secret_key": os.getenv("QUART_SECRET_KEY", "change-me-in-env"),
    "debug": conf("server.debug", False),
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
    "animation_styles": conf("ui.animation_styles", ["wave"]),
    "background_styles": conf("ui.background_styles", ["gradient"]),
    "minimal_mode": {
        "enabled": conf("ui.minimal_mode.enabled", False),
        "hide_elements": conf("ui.minimal_mode.hide_elements", [])
    }
}

LYRICS = {
    "display": {
        "buffer_size": conf("lyrics.display.buffer_size", 6),
        "update_interval": conf("lyrics.display.update_interval", 0.1),
        "idle_interval": conf("lyrics.display.idle_interval", 5.0),
        "latency_compensation": conf("lyrics.display.latency_compensation", 0.1),
        "idle_wait_time": conf("lyrics.display.idle_wait_time", 3.0),
        "smart_race_timeout": conf("lyrics.display.smart_race_timeout", 3.0),
    },
}

SPOTIFY = {
    "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
    "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
    "redirect_uri": conf("spotify.redirect_uri", "http://localhost:9012/callback"),
    "scope": ["user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing"],
    "cache": {
        "metadata_ttl": conf("spotify.cache.metadata_ttl", 2.0),
        "enabled": conf("spotify.cache.enabled", True),
    }
}

PROVIDERS = {
    "lrclib": {
        "enabled": conf("providers.lrclib.enabled", True),
        "priority": conf("providers.lrclib.priority", 1),
        "base_url": "https://lrclib.net/api",
        "timeout": conf("providers.lrclib.timeout", 10),
        "retries": conf("providers.lrclib.retries", 3),
        "cache_duration": conf("providers.lrclib.cache_duration", 86400)
    },
    "spotify": {
        "enabled": conf("providers.spotify.enabled", True),
        "priority": conf("providers.spotify.priority", 2),
        "base_url": os.getenv("SPOTIFY_BASE_URL", "https://fake-spotify-lyrics-api-azure.vercel.app"),
        "timeout": conf("providers.spotify.timeout", 10),
        "retries": conf("providers.spotify.retries", 3),
        "cache_duration": conf("providers.spotify.cache_duration", 3600)
    },
    "qq": {
        "enabled": conf("providers.qq.enabled", True),
        "priority": conf("providers.qq.priority", 4),
        "timeout": conf("providers.qq.timeout", 10),
        "retries": conf("providers.qq.retries", 3),
        "cache_duration": conf("providers.qq.cache_duration", 86400)
    },
    "netease": {
        "enabled": conf("providers.netease.enabled", True),
        "priority": conf("providers.netease.priority", 3),
        "timeout": conf("providers.netease.timeout", 10),
        "retries": conf("providers.netease.retries", 3),
        "cache_duration": conf("providers.netease.cache_duration", 86400)
    },
    "musicxmatch": {
        "enabled": conf("providers.musicxmatch.enabled", True),
        "priority": conf("providers.musicxmatch.priority", 5),
        "timeout": conf("providers.musicxmatch.timeout", 10),
        "retries": conf("providers.musicxmatch.retries", 3),
        "cache_duration": conf("providers.musicxmatch.cache_duration", 86400)
    }
}

STORAGE = {
    "lyrics_db": {
        "enabled": conf("storage.lyrics_db.enabled", True),
        "max_size_mb": conf("storage.lyrics_db.max_size_mb", 100),
        "cleanup_threshold": conf("storage.lyrics_db.cleanup_threshold", 0.9),
        "file_pattern": conf("storage.lyrics_db.file_pattern", "*.json")
    },
    "cache": {
        "enabled": conf("storage.cache.enabled", True),
        "duration_days": conf("storage.cache.duration_days", 30),
        "max_size_mb": conf("storage.cache.max_size_mb", 50),
        "memory_items": conf("storage.cache.memory_items", 100)
    }
}

NOTIFICATIONS = {
    "enabled": conf("notifications.enabled", True),
    "duration": conf("notifications.duration", 3),
    "icon_path": conf("notifications.icon_path", str(RESOURCES_DIR / "images" / "icon.ico"))
}

MEDIA_SOURCE = {
    "sources": [
        {
            "name": "spotify",
            "enabled": conf("media_source.spotify.enabled", True),
            "priority": conf("media_source.spotify.priority", 2),
        },
        {
            "name": "windows_media",
            "enabled": conf("media_source.windows_media.enabled", True),
            "priority": conf("media_source.windows_media.priority", 1),
        },
        {
            "name": "gnome",
            "enabled": conf("media_source.gnome.enabled", False),
            "priority": conf("media_source.gnome.priority", 2),
        }
    ]
}

SYSTEM = {
    "windows": {
        "media_session": {
            "enabled": conf("system.windows.media_session.enabled", True),
            "preferred": conf("system.windows.media_session.preferred", True),
            "timeout": conf("system.windows.media_session.timeout", 5)
        },
    },
    "linux": {
        "gsettings_enabled": conf("system.linux.gsettings_enabled", True),
        "playerctl_required": conf("system.linux.playerctl_required", True)
    }
}

FEATURES = {
    "minimal_ui": conf("features.minimal_ui", False),
    "save_lyrics_locally": conf("features.save_lyrics_locally", True),
    "show_lyrics_source": conf("features.show_lyrics_source", True),
    "parallel_provider_fetch": conf("features.parallel_provider_fetch", True),
    "provider_stats": conf("features.provider_stats", False),
    "auto_theme": conf("features.auto_theme", True),
    "album_art_colors": conf("features.album_art_colors", True)
}

ALBUM_ART = {
    "timeout": conf("album_art.timeout", 5),
    "retries": conf("album_art.retries", 2),
    "lastfm_api_key": conf("album_art.lastfm_api_key"),
    "enable_itunes": conf("album_art.enable_itunes", True),
    "enable_lastfm": conf("album_art.enable_lastfm", True),
    "enable_spotify_enhanced": conf("album_art.enable_spotify_enhanced", True),
    "min_resolution": conf("album_art.min_resolution", 1000)
}

# Helper functions
def get_provider_config(name: str) -> dict:
    return PROVIDERS.get(name, {"enabled": False, "priority": 0})

def is_provider_enabled(name: str) -> bool:
    return PROVIDERS.get(name, {}).get("enabled", False)

def get_provider_priority(name: str) -> int:
    return PROVIDERS.get(name, {}).get("priority", 0)