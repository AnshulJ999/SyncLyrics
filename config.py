"""
SyncLyrics Configuration File
All configurable settings are centralized here for easy management.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

def get_env(key: str, default: str = None) -> str:
    """Get environment variable with default value"""
    return os.getenv(key, default)

def get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean environment variable with default value"""
    return str(os.getenv(key, str(default))).lower() == 'true'

def get_env_int(key: str, default: int = 0) -> int:
    """Get integer environment variable with default value"""
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default

# ==========================================
# Development and Debug Settings
# ==========================================

DEBUG = {
    "enabled": get_env_bool("DEBUG", False),
    "log_file": get_env("LOG_FILE", "synclyrics.log"),
    "log_level": get_env("LOG_LEVEL", "WARNING").upper(),
    "log_providers": get_env_bool("LOG_PROVIDERS", True),
    "log_polling": True,  # Add this to control polling logs
    "log_to_console": get_env_bool("LOG_TO_CONSOLE", True),
    "log_detailed": get_env_bool("LOG_DETAILED", False),
    "performance_logging": get_env_bool("PERFORMANCE_LOGGING", False),
    "log_rotation": {
        "max_bytes": get_env_int("LOG_MAX_BYTES", 10*1024*1024),  # 10MB
        "backup_count": get_env_int("LOG_BACKUP_COUNT", 5)
    }
}

# Media Source Configuration
MEDIA_SOURCE = {
    "sources": [
        {
            "name": "spotify",
            "enabled": True,
            "priority": 2,  
        },
        {
            "name": "windows_media",
            "enabled": True,
            "priority": 1,
        },
        {
            "name": "gnome",
            "enabled": False,
            "priority": 2,
        }
    ]
}

# ==========================================
# Base Paths and Directories
# ==========================================
ROOT_DIR = Path(__file__).parent
RESOURCES_DIR = ROOT_DIR / "resources"
DATABASE_DIR = ROOT_DIR / "lyrics_database"
CACHE_DIR = ROOT_DIR / "cache"

# Create necessary directories
for directory in [ROOT_DIR, RESOURCES_DIR, DATABASE_DIR, CACHE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ==========================================
# Server Configuration
# ==========================================
SERVER = {
    "port": get_env_int("PORT", 9012),
    "host": get_env("HOST", "0.0.0.0"),
    "secret_key": get_env("FLASK_SECRET_KEY", "your-secret-key-here"),
    "debug": get_env_bool("DEBUG", False),
}

# ==========================================
# UI and Display Settings
# ==========================================
UI = {
    "themes": {
        "default": {
            "bg_start": "#24273a",
            "bg_end": "#363b54",
            "text": "#ffffff"
        },
        "dark": {
            "bg_start": "#1c1c1c",
            "bg_end": "#2c2c2c",
            "text": "#ffffff"
        },
        "light": {
            "bg_start": "#ffffff",
            "bg_end": "#f0f0f0",
            "text": "#000000"
        }
    },
    "animation_styles": ["wave", "fade", "slide", "none"],
    "background_styles": ["gradient", "solid", "albumart"],
    "minimal_mode": {
        "enabled": True,
        "hide_elements": ["bottom-nav", "provider-info", "minimal-toggle"]
    }
}

# ==========================================
# Lyrics Configuration
# ==========================================
# It's recommended to fine-tune latency_compensation to your preference.
LYRICS = {
    "display": {
        "buffer_size": 6,  # Number of lyrics lines to display (previous + current + next)
        "update_interval": 0.1,  # Seconds between active polling updates (100ms)
        "idle_interval": 5.0,    # Idle polling (3 seconds)
        "latency_compensation": 0.17,  # Positive = earlier, negative = later (100ms earlier)
        "idle_wait_time": 3.0,   # Wait time before switching to idle mode (seconds)
    },
}

# Spotify API Configuration
SPOTIFY = {
    "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
    "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
    "redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:9012/callback"),
    "scope": [
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing"
    ],
    "cache": {
        "metadata_ttl": 3.0,  # Time to live for metadata cache in seconds
        "enabled": True,      # Enable/disable caching
    }
}
# ==========================================
# Lyrics Providers Configuration
# ==========================================

PROVIDERS = {

    "lrclib": {
        "enabled": True,
        "priority": 1,
        "base_url": "https://lrclib.net/api",
        "timeout": 10,
        "retries": 3,
        "cache_duration": 86400  # 24 hours in seconds
    },

    "spotify": {
        "enabled": True,
        "priority": 2,
        "base_url": get_env("SPOTIFY_LYRICS_SERVER", "https://spotify-lyrics-api-azure.vercel.app"),
        "api_url": "https://api.spotify.com/v1",
        "timeout": get_env_int("SPOTIFY_TIMEOUT", 10),
        "retries": get_env_int("SPOTIFY_RETRIES", 3),
        "client_id": get_env("SPOTIFY_CLIENT_ID", ""),
        "client_secret": get_env("SPOTIFY_CLIENT_SECRET", ""),
        "token_refresh_buffer": 300,
        "cache_duration": 3600
    },
    "qq": {
        "enabled": True,
        "priority": 4,
        "timeout": 10,
        "retries": 3,
        "cache_duration": 86400  # 24 hours in seconds
    },
    "netease": {
        "enabled": True,
        "priority": 3,
        "base_url": "https://music.163.com/api",
        "timeout": get_env_int("NETEASE_TIMEOUT", 10),
        "retries": get_env_int("NETEASE_RETRIES", 3),
        "cache_duration": 86400,
        "cookie": get_env("NETEASE_COOKIE", "NMTID=00OAVK3xqDG726ITU6jopU6jF2yMk0AAAGCO8l1BA; JSESSIONID-WYYY=8KQo11YK2GZP45RMlz8Kn80vHZ9%2FGvwzRKQXXy0iQoFKycWdBlQjbfT0MJrFa6hwRfmpfBYKeHliUPH287JC3hNW99WQjrh9b9RmKT%2Fg1Exc2VwHZcsqi7ITxQgfEiee50po28x5xTTZXKoP%2FRMctN2jpDeg57kdZrXz%2FD%2FWghb%5C4DuZ%3A1659124633932; _iuqxldmzr_=32; _ntes_nnid=0db6667097883aa9596ecfe7f188c3ec,1659122833973; _ntes_nuid=0db6667097883aa9596ecfe7f188c3ec; WNMCID=xygast.1659122837568.01.0; WEVNSM=1.0.0; WM_NI=CwbjWAFbcIzPX3dsLP%2F52VB%2Bxr572gmqAYwvN9KU5X5f1nRzBYl0SNf%2BV9FTmmYZy%2FoJLADaZS0Q8TrKfNSBNOt0HLB8rRJh9DsvMOT7%2BCGCQLbvlWAcJBJeXb1P8yZ3RHA%3D; WM_NIKE=9ca17ae2e6ffcda170e2e6ee90c65b85ae87b9aa5483ef8ab3d14a939e9a83c459959caeadce47e991fbaee82af0fea7c3b92a81a9ae8bd64b86beadaaf95c9cedac94cf5cedebfeb7c121bcaefbd8b16dafaf8fbaf67e8ee785b6b854f7baff8fd1728287a4d1d246a6f59adac560afb397bbfc25ad9684a2c76b9a8d00b2bb60b295aaafd24a8e91bcd1cb4882e8beb3c964fb9cbd97d04598e9e5a4c6499394ae97ef5d83bd86a3c96f9cbeffb1bb739aed9ea9c437e2a3; WM_TID=AAkRFnl03RdABEBEQFOBWHCPOeMra4IL; playerid=94262567")
    }
}


# ==========================================
# Cache and Storage Settings
# ==========================================

STORAGE = {
    "lyrics_db": {
        "enabled": True,
        "max_size_mb": get_env_int("MAX_LYRICS_DB_SIZE_MB", 100),
        "cleanup_threshold": 0.9,   # Clean when 90% full
        "file_pattern": "*.json"
    },
    "cache": {
        "enabled": True,
        "duration_days": get_env_int("CACHE_DURATION_DAYS", 30),
        "max_size_mb": get_env_int("MAX_CACHE_SIZE_MB", 50),
        "memory_items": get_env_int("MAX_MEMORY_CACHE_ITEMS", 100) # Maximum items to keep in memory
    }
}

# ==========================================
# Notification Settings
# ==========================================
NOTIFICATIONS = {
    "enabled": True,
    "duration": 3,  # seconds
    "icon_path": str(RESOURCES_DIR / "images" / "icon.ico")
}

# ==========================================
# System-specific Settings
# ==========================================
SYSTEM = {
    "windows": {
        "media_session": {
            "enabled": True,
            "preferred": True,
            "timeout": 5
        },
    },
    "linux": {
        "gsettings_enabled": True,
        "playerctl_required": True
    }
}


# ==========================================
# Feature Flags
# ==========================================

FEATURES = {
    "minimal_ui": get_env_bool("MINIMAL_MODE", True), # Enable minimal UI mode
    "save_lyrics_locally": get_env_bool("SAVE_LYRICS_LOCALLY", True), # Save found lyrics to database
    "show_lyrics_source": get_env_bool("SHOW_LYRICS_SOURCE", True),      # Show which provider found the lyrics
    "parallel_provider_fetch": get_env_bool("ENABLE_PARALLEL_FETCH", True), # Try multiple providers simultaneously
    "provider_stats": get_env_bool("ENABLE_PROVIDER_STATS", False), # Track provider performance (disabled for simplicity)
    "auto_theme": get_env_bool("ENABLE_AUTO_THEME", True), # Auto-detect system theme
    "album_art_colors": get_env_bool("ENABLE_ALBUM_ART_COLORS", True) # Extract colors from album art if available
}

# Function to get provider configuration
def get_provider_config(name: str) -> dict:
    """Get configuration for a specific provider"""
    return PROVIDERS.get(name, {"enabled": False, "priority": 0})

# Function to check if a provider is enabled
def is_provider_enabled(name: str) -> bool:
    """Check if a provider is enabled"""
    return PROVIDERS.get(name, {}).get("enabled", False)

# Function to get provider priority
def get_provider_priority(name: str) -> int:
    """Get priority level for a provider"""
    return PROVIDERS.get(name, {}).get("priority", 0)