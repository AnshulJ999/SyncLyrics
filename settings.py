"""
SyncLyrics Settings Manager
Handles dynamic configuration management using settings.json
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass
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
            "debug.enabled": Setting("Debug Mode", bool, False, True, "Debug"),
            "debug.log_file": Setting("Log File", str, "synclyrics.log", True, "Debug"),
            "debug.log_level": Setting("Log Level", str, "WARNING", True, "Debug"),
            "debug.log_providers": Setting("Log Providers", bool, True, False, "Debug"),
            "debug.log_polling": Setting("Log Polling", bool, True, False, "Debug"),
            "debug.log_to_console": Setting("Log to Console", bool, True, False, "Debug"),
            "debug.log_detailed": Setting("Detailed Logging", bool, False, False, "Debug"),
            "debug.performance_logging": Setting("Performance Logging", bool, False, False, "Debug"),
            "debug.log_rotation.max_bytes": Setting("Max Log Size", int, 10485760, False, "Debug"),
            "debug.log_rotation.backup_count": Setting("Log Backups", int, 5, False, "Debug"),

            # Server
            "server.port": Setting("Port", int, 9012, True, "Server"),
            "server.host": Setting("Host", str, "0.0.0.0", True, "Server"),
            "server.secret_key": Setting("Secret Key", str, "change-me", True, "Server"),
            "server.debug": Setting("Server Debug", bool, False, True, "Server"),

            # UI
            "ui.themes.default.bg_start": Setting("Default Start", str, "#24273a", False, "UI"),
            "ui.themes.default.bg_end": Setting("Default End", str, "#363b54", False, "UI"),
            "ui.themes.default.text": Setting("Default Text", str, "#ffffff", False, "UI"),
            "ui.themes.dark.bg_start": Setting("Dark Start", str, "#1c1c1c", False, "UI"),
            "ui.themes.dark.bg_end": Setting("Dark End", str, "#2c2c2c", False, "UI"),
            "ui.themes.dark.text": Setting("Dark Text", str, "#ffffff", False, "UI"),
            "ui.themes.light.bg_start": Setting("Light Start", str, "#ffffff", False, "UI"),
            "ui.themes.light.bg_end": Setting("Light End", str, "#f0f0f0", False, "UI"),
            "ui.themes.light.text": Setting("Light Text", str, "#000000", False, "UI"),
            "ui.animation_styles": Setting("Animation Styles", list, ["wave", "fade"], False, "UI"),
            "ui.background_styles": Setting("Bg Styles", list, ["gradient", "solid"], False, "UI"),
            "ui.minimal_mode.enabled": Setting("Minimal Mode", bool, False, False, "UI"),
            "ui.minimal_mode.hide_elements": Setting("Hidden Elements", list, ["bottom-nav"], False, "UI"),

            # Lyrics
            "lyrics.display.buffer_size": Setting("Buffer Size", int, 6, False, "Lyrics"),
            "lyrics.display.update_interval": Setting("Update Interval", float, 0.1, False, "Lyrics"),
            "lyrics.display.idle_interval": Setting("Idle Interval", float, 5.0, False, "Lyrics"),
            "lyrics.display.latency_compensation": Setting("Latency Comp", float, 0.1, False, "Lyrics"),
            "lyrics.display.idle_wait_time": Setting("Idle Wait", float, 3.0, False, "Lyrics"),
            "lyrics.display.smart_race_timeout": Setting("Race Timeout", float, 3.0, False, "Lyrics"),

            # Providers
            "providers.lrclib.enabled": Setting("LRCLib", bool, True, True, "Providers"),
            "providers.lrclib.priority": Setting("LRCLib Priority", int, 1, False, "Providers"),
            "providers.lrclib.timeout": Setting("Timeout", int, 10, False, "Providers"),
            "providers.lrclib.retries": Setting("Retries", int, 3, False, "Providers"),
            "providers.lrclib.cache_duration": Setting("Cache", int, 86400, False, "Providers"),

            "providers.spotify.enabled": Setting("Spotify", bool, True, True, "Providers"),
            "providers.spotify.priority": Setting("Priority", int, 2, False, "Providers"),
            "providers.spotify.timeout": Setting("Timeout", int, 10, False, "Providers"),
            "providers.spotify.retries": Setting("Retries", int, 3, False, "Providers"),
            "providers.spotify.token_refresh_buffer": Setting("Buffer", int, 300, False, "Providers"),
            "providers.spotify.cache_duration": Setting("Cache", int, 3600, False, "Providers"),

            "providers.qq.enabled": Setting("QQ", bool, True, True, "Providers"),
            "providers.qq.priority": Setting("Priority", int, 4, False, "Providers"),
            "providers.qq.timeout": Setting("Timeout", int, 10, False, "Providers"),
            "providers.qq.retries": Setting("Retries", int, 3, False, "Providers"),
            "providers.qq.cache_duration": Setting("Cache", int, 86400, False, "Providers"),

            "providers.netease.enabled": Setting("NetEase", bool, True, True, "Providers"),
            "providers.netease.priority": Setting("Priority", int, 3, False, "Providers"),
            "providers.netease.timeout": Setting("Timeout", int, 10, False, "Providers"),
            "providers.netease.retries": Setting("Retries", int, 3, False, "Providers"),
            "providers.netease.cache_duration": Setting("Cache", int, 86400, False, "Providers"),

            # Storage
            "storage.lyrics_db.enabled": Setting("DB Enabled", bool, True, False, "Storage"),
            "storage.lyrics_db.max_size_mb": Setting("Max DB Size", int, 100, False, "Storage"),
            "storage.lyrics_db.cleanup_threshold": Setting("Cleanup", float, 0.9, False, "Storage"),
            "storage.lyrics_db.file_pattern": Setting("Pattern", str, "*.json", False, "Storage"),
            "storage.cache.enabled": Setting("Cache Enabled", bool, True, False, "Storage"),
            "storage.cache.duration_days": Setting("Duration", int, 30, False, "Storage"),
            "storage.cache.max_size_mb": Setting("Max Cache", int, 50, False, "Storage"),
            "storage.cache.memory_items": Setting("Mem Items", int, 100, False, "Storage"),

            # Notifications
            "notifications.enabled": Setting("Notifications", bool, True, False, "Notifications"),
            "notifications.duration": Setting("Duration", int, 3, False, "Notifications"),
            "notifications.icon_path": Setting("Icon", str, "resources/images/icon.ico", False, "Notifications"),

            # System
            "system.windows.media_session.enabled": Setting("Win Media", bool, True, True, "System"),
            "system.windows.media_session.preferred": Setting("Prefer Win", bool, True, True, "System"),
            "system.windows.media_session.timeout": Setting("Timeout", int, 5, False, "System"),
            "system.linux.gsettings_enabled": Setting("GSettings", bool, True, True, "System"),
            "system.linux.playerctl_required": Setting("Playerctl", bool, True, True, "System"),

            # Features
            "features.minimal_ui": Setting("Minimal UI", bool, False, False, "Features"),
            "features.save_lyrics_locally": Setting("Save Local", bool, True, False, "Features"),
            "features.show_lyrics_source": Setting("Show Source", bool, True, False, "Features"),
            "features.parallel_provider_fetch": Setting("Parallel", bool, True, False, "Features"),
            "features.provider_stats": Setting("Stats", bool, False, False, "Features"),
            "features.auto_theme": Setting("Auto Theme", bool, True, False, "Features"),
            "features.album_art_colors": Setting("Art Colors", bool, True, False, "Features"),

            # Media Source
            "media_source.spotify.enabled": Setting("Spotify Source", bool, True, True, "Media"),
            "media_source.spotify.priority": Setting("Priority", int, 2, False, "Media"),
            "media_source.windows_media.enabled": Setting("Win Source", bool, True, True, "Media"),
            "media_source.windows_media.priority": Setting("Priority", int, 1, False, "Media"),
            "media_source.gnome.enabled": Setting("Gnome Source", bool, False, True, "Media"),
            "media_source.gnome.priority": Setting("Priority", int, 2, False, "Media"),
            
            # Spotify API
            "spotify.client_id": Setting("Client ID", str, "", True, "Spotify API"),
            "spotify.client_secret": Setting("Client Secret", str, "", True, "Spotify API"),
            "spotify.redirect_uri": Setting("Redirect URI", str, "http://localhost:9012/callback", True, "Spotify API"),
            "spotify.base_url": Setting("API URL", str, "https://spotify-lyrics-api-azure.vercel.app", True, "Spotify API"),
            "spotify.cache.metadata_ttl": Setting("Metadata TTL", float, 3.0, False, "Spotify API"),
            "spotify.cache.enabled": Setting("Cache Enabled", bool, True, False, "Spotify API"),

            # Paths (Read Only / Defaults)
            "paths.root_dir": Setting("Root", str, ".", True, "Paths"),
            "paths.resources_dir": Setting("Resources", str, "resources", True, "Paths"),
            "paths.database_dir": Setting("Database", str, "lyrics_database", True, "Paths"),
            "paths.cache_dir": Setting("Cache", str, "cache", True, "Paths"),
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
                    # Update keys that exist in definitions
                    for key, val in saved.items():
                        if key in self._definitions:
                            self._settings[key] = self._definitions[key].validate_and_convert(val)
            except Exception as e:
                logger.error(f"Failed to load settings.json: {e}")

    def get(self, key: str) -> Any:
        return self._settings.get(key, self._definitions[key].default)

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
                "requires_restart": defin.requires_restart
            }
        return result

    def reset_to_defaults(self):
        if SETTINGS_FILE.exists():
            os.remove(SETTINGS_FILE)
        self.load_settings()

settings = SettingsManager()
