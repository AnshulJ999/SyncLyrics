"""
SyncLyrics Settings Manager
Handles dynamic configuration management using settings.json
"""

import json
import shutil
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, asdict
from logging_config import get_logger

logger = get_logger(__name__)

SETTINGS_FILE = Path(__file__).parent / "settings.json"

@dataclass
class Setting:
    """Represents a single configurable setting"""
    name: str
    type: type
    default: Any
    requires_restart: bool = False
    category: Optional[str] = None
    description: Optional[str] = None
    widget_type: str = "text"  # text, number, slider, switch, select, color
    options: Optional[list] = None  # For select
    min_val: Optional[float] = None  # For slider/number
    max_val: Optional[float] = None  # For slider/number

    def validate_and_convert(self, value: Any) -> Any:
        try:
            if self.type == bool and isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return self.type(value)
        except (ValueError, TypeError):
            return self.default

class SettingsManager:
    def __init__(self):
        self._settings: Dict[str, Any] = {}
        
        # Define all available settings
        self._definitions = {
            # Debug
            "debug.enabled": Setting("Debug Mode", bool, False, True, "Debug", "Enable debug features", "switch"),
            "debug.log_file": Setting("Log File", str, "synclyrics.log", True, "Debug", "Log file name"),
            "debug.log_level": Setting("Log Level", str, "WARNING", True, "Debug", "Logging verbosity", "select", options=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
            "debug.log_providers": Setting("Log Providers", bool, True, False, "Debug", "Log provider requests", "switch"),
            "debug.log_polling": Setting("Log Polling", bool, True, False, "Debug", "Log polling events", "switch"),
            "debug.log_to_console": Setting("Log to Console", bool, True, False, "Debug", "Print logs to terminal", "switch"),
            "debug.log_detailed": Setting("Detailed Logging", bool, False, False, "Debug", "Include detailed info", "switch"),
            "debug.performance_logging": Setting("Performance Logging", bool, False, False, "Debug", "Log timing stats", "switch"),
            "debug.log_rotation.max_bytes": Setting("Max Log Size", int, 10485760, False, "Debug", "Max log file size (bytes)", "number"),
            "debug.log_rotation.backup_count": Setting("Log Backups", int, 5, False, "Debug", "Number of backups to keep", "number"),

            # Server
            "server.port": Setting("Port", int, 9012, True, "Server", "Server port", "number"),
            "server.host": Setting("Host", str, "0.0.0.0", True, "Server", "Bind address"),
            "server.debug": Setting("Server Debug", bool, False, True, "Server", "Quart debug mode", "switch"),

            # UI
            "ui.themes.default.bg_start": Setting("Default Start", str, "#24273a", False, "UI", "Default gradient start", "color"),
            "ui.themes.default.bg_end": Setting("Default End", str, "#363b54", False, "UI", "Default gradient end", "color"),
            "ui.themes.default.text": Setting("Default Text", str, "#ffffff", False, "UI", "Default text color", "color"),
            "ui.themes.dark.bg_start": Setting("Dark Start", str, "#1c1c1c", False, "UI", "Dark mode gradient start", "color"),
            "ui.themes.dark.bg_end": Setting("Dark End", str, "#2c2c2c", False, "UI", "Dark mode gradient end", "color"),
            "ui.themes.dark.text": Setting("Dark Text", str, "#ffffff", False, "UI", "Dark mode text color", "color"),
            "ui.themes.light.bg_start": Setting("Light Start", str, "#ffffff", False, "UI", "Light mode gradient start", "color"),
            "ui.themes.light.bg_end": Setting("Light End", str, "#f0f0f0", False, "UI", "Light mode gradient end", "color"),
            "ui.themes.light.text": Setting("Light Text", str, "#000000", False, "UI", "Light mode text color", "color"),
            "ui.animation_styles": Setting("Animation Styles", list, ["wave", "fade"], False, "UI", "Enabled animations"),
            "ui.background_styles": Setting("Bg Styles", list, ["gradient", "solid"], False, "UI", "Enabled backgrounds"),
            "ui.minimal_mode.enabled": Setting("Minimal Mode", bool, False, False, "UI", "Hide extra UI elements", "switch"),
            "ui.minimal_mode.hide_elements": Setting("Hidden Elements", list, ["bottom-nav"], False, "UI", "Elements to hide in minimal mode"),
            "ui.blur_strength": Setting("Blur Strength", int, 10, False, "UI", "Background blur (px)", "slider", min_val=0, max_val=50),
            "ui.overlay_opacity": Setting("Overlay Opacity", float, 0.4, False, "UI", "Background overlay opacity", "slider", min_val=0.0, max_val=1.0),
            "ui.sharp_album_art": Setting("Sharp Album Art", bool, False, False, "UI", "Disable background blur & scaling", "switch"),
            "ui.soft_album_art": Setting("Soft Album Art", bool, False, False, "UI", "Medium blur album art background", "switch"),

            # Lyrics
            "lyrics.display.buffer_size": Setting("Buffer Size", int, 6, False, "Lyrics", "Lines to buffer", "number", min_val=1, max_val=20),
            "lyrics.display.update_interval": Setting("Update Interval", float, 0.1, False, "Lyrics", "UI refresh rate (s)", "slider", min_val=0.05, max_val=1.0),
            "lyrics.display.idle_interval": Setting("Idle Interval", float, 5.0, False, "Lyrics", "Check rate when idle (s)", "slider", min_val=1.0, max_val=30.0),
            "lyrics.display.latency_compensation": Setting("Latency Comp", float, 0.1, False, "Lyrics", "Audio sync offset (s)", "slider", min_val=-2.0, max_val=2.0),
            "lyrics.display.idle_wait_time": Setting("Idle Wait", float, 3.0, False, "Lyrics", "Time before idle (s)", "slider", min_val=1.0, max_val=10.0),
            "lyrics.display.smart_race_timeout": Setting("Race Timeout", float, 3.0, False, "Lyrics", "Provider race timeout (s)", "slider", min_val=1.0, max_val=10.0),

            # Providers
            "providers.lrclib.enabled": Setting("LRCLib", bool, True, True, "Providers", "Enable LRCLib", "switch"),
            "providers.lrclib.priority": Setting("LRCLib Priority", int, 1, False, "Providers", "Fetch priority", "number", min_val=1, max_val=10),
            "providers.lrclib.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number"),
            "providers.lrclib.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number"),
            "providers.lrclib.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number"),

            "providers.spotify.enabled": Setting("Spotify", bool, True, True, "Providers", "Enable Spotify Lyrics", "switch"),
            "providers.spotify.priority": Setting("Priority", int, 2, False, "Providers", "Fetch priority", "number", min_val=1, max_val=10),
            "providers.spotify.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number"),
            "providers.spotify.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number"),
            "providers.spotify.token_refresh_buffer": Setting("Buffer", int, 300, False, "Providers", "Token refresh buffer (s)", "number"),
            "providers.spotify.cache_duration": Setting("Cache", int, 3600, False, "Providers", "Cache TTL (s)", "number"),

            "providers.qq.enabled": Setting("QQ", bool, True, True, "Providers", "Enable QQ Music", "switch"),
            "providers.qq.priority": Setting("Priority", int, 4, False, "Providers", "Fetch priority", "number", min_val=1, max_val=10),
            "providers.qq.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number"),
            "providers.qq.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number"),
            "providers.qq.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number"),

            "providers.netease.enabled": Setting("NetEase", bool, True, True, "Providers", "Enable NetEase", "switch"),
            "providers.netease.priority": Setting("Priority", int, 3, False, "Providers", "Fetch priority", "number", min_val=1, max_val=10),
            "providers.netease.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number"),
            "providers.netease.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number"),
            "providers.netease.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number"),

            "providers.musicxmatch.enabled": Setting("Musicxmatch", bool, True, True, "Providers", "Enable Musicxmatch", "switch"),
            "providers.musicxmatch.priority": Setting("Priority", int, 2, False, "Providers", "Fetch priority", "number", min_val=1, max_val=10),
            "providers.musicxmatch.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number"),
            "providers.musicxmatch.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number"),
            "providers.musicxmatch.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number"),

            # Storage
            "storage.lyrics_db.enabled": Setting("DB Enabled", bool, True, False, "Storage", "Enable local DB", "switch"),
            "storage.lyrics_db.max_size_mb": Setting("Max DB Size", int, 100, False, "Storage", "Max DB size (MB)", "number"),
            "storage.lyrics_db.cleanup_threshold": Setting("Cleanup", float, 0.9, False, "Storage", "Cleanup threshold (0-1)", "slider", min_val=0.1, max_val=1.0),
            "storage.lyrics_db.file_pattern": Setting("Pattern", str, "*.json", False, "Storage", "File pattern"),
            "storage.cache.enabled": Setting("Cache Enabled", bool, True, False, "Storage", "Enable caching", "switch"),
            "storage.cache.duration_days": Setting("Duration", int, 30, False, "Storage", "Cache duration (days)", "number"),
            "storage.cache.max_size_mb": Setting("Max Cache", int, 50, False, "Storage", "Max cache size (MB)", "number"),
            "storage.cache.memory_items": Setting("Mem Items", int, 100, False, "Storage", "Max memory items", "number"),

            # Notifications
            "notifications.enabled": Setting("Notifications", bool, True, False, "Notifications", "Enable notifications", "switch"),
            "notifications.duration": Setting("Duration", int, 3, False, "Notifications", "Notification duration (s)", "number"),
            "notifications.icon_path": Setting("Icon", str, "resources/images/icon.ico", False, "Notifications", "Icon path"),

            # System
            "system.windows.media_session.enabled": Setting("Win Media", bool, True, True, "System", "Enable Windows Media", "switch"),
            "system.windows.media_session.preferred": Setting("Prefer Win", bool, True, True, "System", "Prefer Windows Media", "switch"),
            "system.windows.media_session.timeout": Setting("Timeout", int, 5, False, "System", "SMTC timeout (s)", "number"),
            "system.linux.gsettings_enabled": Setting("GSettings", bool, True, True, "System", "Enable GSettings", "switch"),
            "system.linux.playerctl_required": Setting("Playerctl", bool, True, True, "System", "Require Playerctl", "switch"),

            # Features
            "features.minimal_ui": Setting("Minimal UI", bool, False, False, "Features", "Enable minimal mode", "switch"),
            "features.save_lyrics_locally": Setting("Save Local", bool, True, False, "Features", "Save lyrics to disk", "switch"),
            "features.show_lyrics_source": Setting("Show Source", bool, True, False, "Features", "Show provider name", "switch"),
            "features.parallel_provider_fetch": Setting("Parallel", bool, True, False, "Features", "Fetch concurrently", "switch"),
            "features.provider_stats": Setting("Stats", bool, False, False, "Features", "Track provider stats", "switch"),
            "features.auto_theme": Setting("Auto Theme", bool, True, False, "Features", "Auto-switch theme", "switch"),
            "features.album_art_colors": Setting("Art Colors", bool, True, False, "Features", "Use album art colors", "switch"),
            "features.album_art_db": Setting("Album Art DB", bool, True, False, "Features", "Enable album art database", "switch"),

            # Media Source
            "media_source.spotify.enabled": Setting("Spotify Source", bool, True, True, "Media", "Enable Spotify source", "switch"),
            "media_source.spotify.priority": Setting("Priority", int, 2, False, "Media", "Source priority", "number"),
            "media_source.windows_media.enabled": Setting("Win Source", bool, True, True, "Media", "Enable Windows source", "switch"),
            "media_source.windows_media.priority": Setting("Priority", int, 1, False, "Media", "Source priority", "number"),
            "media_source.gnome.enabled": Setting("Gnome Source", bool, False, True, "Media", "Enable Gnome source", "switch"),
            "media_source.gnome.priority": Setting("Priority", int, 2, False, "Media", "Source priority", "number"),
            
            # Spotify API
            "spotify.redirect_uri": Setting("Redirect URI", str, "http://localhost:9012/callback", True, "Spotify API", "Callback URL"),
            "spotify.cache.metadata_ttl": Setting("Metadata TTL", float, 3.0, False, "Spotify API", "Metadata cache (s)", "number"),
            "spotify.cache.enabled": Setting("Cache Enabled", bool, True, False, "Spotify API", "Enable API cache", "switch"),
            
            # Album Art
            "album_art.timeout": Setting("Timeout", int, 5, False, "Album Art", "Request timeout (s)", "number", min_val=1, max_val=30),
            "album_art.retries": Setting("Retries", int, 2, False, "Album Art", "Max retries", "number", min_val=0, max_val=5),
            "album_art.enable_itunes": Setting("iTunes", bool, True, False, "Album Art", "Enable iTunes source", "switch"),
            "album_art.enable_lastfm": Setting("Last.fm", bool, True, False, "Album Art", "Enable Last.fm source", "switch"),
            "album_art.enable_spotify_enhanced": Setting("Spotify Enhanced", bool, True, False, "Album Art", "Try to enhance Spotify URLs", "switch"),
            "album_art.min_resolution": Setting("Min Resolution", int, 3000, False, "Album Art", "Preferred resolution (px)", "number", min_val=640, max_val=3000),
            
            # Visual Mode
            "visual_mode.enabled": Setting("Visual Mode", bool, True, False, "Visual Mode", "Enable visual mode for instrumentals", "switch"),
            "visual_mode.delay_seconds": Setting("Delay", int, 10, False, "Visual Mode", "Delay before hiding lyrics (s)", "slider", min_val=1, max_val=60),
            "visual_mode.auto_sharp": Setting("Auto Sharp", bool, True, False, "Visual Mode", "Auto-switch to sharp mode in visual mode", "switch"),
            "visual_mode.slideshow.enabled": Setting("Slideshow", bool, True, False, "Visual Mode", "Enable slideshow when no music", "switch"),
            "visual_mode.slideshow.interval_seconds": Setting("Slideshow Speed", int, 360, False, "Visual Mode", "Seconds per image", "slider", min_val=3, max_val=3600),
        }
        
        self.load_settings()

    def load_settings(self) -> None:
        """Load settings from JSON, fall back to defaults"""
        self._settings = {}
        
        # 1. Load defaults first
        for key, definition in self._definitions.items():
            self._settings[key] = definition.default

        # 2. Load from JSON if exists
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    saved = json.load(f)
                    # Update keys
                    for key, val in saved.items():
                        # LENIENT MODE: Allow loading keys even if not in definitions
                        if key in self._definitions:
                            # Type conversion if known
                            self._settings[key] = self._definitions[key].validate_and_convert(val)
                        else:
                            # Store as-is if unknown
                            self._settings[key] = val
            except Exception as e:
                logger.error(f"Failed to load settings.json: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value.
        Priority:
        1. Loaded value (from JSON or Schema Default)
        2. Schema Default (if key in definitions but not in settings dict yet)
        3. Provided 'default' argument (if key unknown)
        """
        if key in self._settings:
            return self._settings[key]
        
        if key in self._definitions:
            return self._definitions[key].default
            
        return default

    def set(self, key: str, value: Any) -> bool:
        if key not in self._definitions:
            return False
        
        setting = self._definitions[key]
        converted = setting.validate_and_convert(value)
        self._settings[key] = converted
        return setting.requires_restart

    def save_to_config(self) -> None:
        """Save current memory settings to JSON file"""
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self._settings, f, indent=4, sort_keys=True)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

    def get_all(self) -> Dict:
        """Return formatted settings for UI"""
        result = {}
        for key, val in self._settings.items():
            defin = self._definitions[key]
            cat = defin.category or "Misc"
            if cat not in result: result[cat] = {}
            
            result[cat][key] = {
                "value": val,
                "name": defin.name,
                "description": defin.description,
                "type": defin.type.__name__,
                "requires_restart": defin.requires_restart,
                "widget_type": defin.widget_type,
                "options": defin.options,
                "min": defin.min_val,
                "max": defin.max_val
            }
        return result

    def reset_to_defaults(self):
        if SETTINGS_FILE.exists():
            os.remove(SETTINGS_FILE)
        self.load_settings()

settings = SettingsManager()
