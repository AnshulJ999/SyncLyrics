"""
SyncLyrics Settings Manager
Handles dynamic configuration management using settings.json
"""

import json
import shutil
import os
import sys
import uuid
import ast  # FIX: For safe list parsing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, asdict
from logging_config import get_logger

logger = get_logger(__name__)

# Allow overriding the settings file location via environment variable
# This is crucial for HAOS/Docker persistence
if "__compiled__" in globals() or getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).parent

SETTINGS_FILE = Path(os.getenv("SYNCLYRICS_SETTINGS_FILE", str(ROOT_DIR / "settings.json")))

@dataclass
class Setting:
    """Represents a single configurable setting"""
    name: str
    type: type
    default: Any
    requires_restart: bool = False
    category: Optional[str] = None
    description: Optional[str] = None
    widget_type: str = "text"  # text, number, slider, switch, select, color, list
    options: Optional[list] = None  # For select
    min_val: Optional[float] = None  # For slider/number
    max_val: Optional[float] = None  # For slider/number
    deprecated: bool = False  # Mark settings that are not actively used
    advanced: bool = False  # Hide from main view, show under "Advanced" dropdown

    def validate_and_convert(self, value: Any) -> Any:
        try:
            if self.type == bool and isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            
            # FIX: Robust list handling using ast.literal_eval
            if self.type == list:
                if isinstance(value, list):
                    return value  # Already a list
                if isinstance(value, str):
                    value = value.strip()
                    # Method 1: Try ast.literal_eval (handles ['a'] and ["a"])
                    try:
                        parsed = ast.literal_eval(value)
                        if isinstance(parsed, list):
                            return parsed
                    except (ValueError, SyntaxError):
                        pass
                    # Method 2: JSON fallback (handles ["a", "b"])
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, list):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    # Method 3: Comma separation (strip brackets first!)
                    clean_value = value.strip("[]")
                    if clean_value:
                        return [v.strip().strip("'").strip('"') for v in clean_value.split(',') if v.strip()]
                    return []  # Empty list
                return self.default  # Invalid type
            
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

            # UI - Active settings
            "ui.blur_strength": Setting("Blur Strength", int, 10, False, "UI", "Background blur (px)", "slider", min_val=0, max_val=50),
            "ui.overlay_opacity": Setting("Overlay Opacity", float, 0.4, False, "UI", "Background overlay opacity", "slider", min_val=0.0, max_val=1.0),
            "ui.sharp_album_art": Setting("Sharp Album Art", bool, False, False, "UI", "Disable background blur & scaling", "switch"),
            "ui.soft_album_art": Setting("Soft Album Art", bool, False, False, "UI", "Medium blur album art background", "switch"),
            
            # UI - Deprecated (handled by frontend CSS/JS, not backend)
            "ui.themes.default.bg_start": Setting("Default Start", str, "#24273a", False, "Deprecated", "Default gradient start", "color", deprecated=True),
            "ui.themes.default.bg_end": Setting("Default End", str, "#363b54", False, "Deprecated", "Default gradient end", "color", deprecated=True),
            "ui.themes.default.text": Setting("Default Text", str, "#ffffff", False, "Deprecated", "Default text color", "color", deprecated=True),
            "ui.themes.dark.bg_start": Setting("Dark Start", str, "#1c1c1c", False, "Deprecated", "Dark mode gradient start", "color", deprecated=True),
            "ui.themes.dark.bg_end": Setting("Dark End", str, "#2c2c2c", False, "Deprecated", "Dark mode gradient end", "color", deprecated=True),
            "ui.themes.dark.text": Setting("Dark Text", str, "#ffffff", False, "Deprecated", "Dark mode text color", "color", deprecated=True),
            "ui.themes.light.bg_start": Setting("Light Start", str, "#ffffff", False, "Deprecated", "Light mode gradient start", "color", deprecated=True),
            "ui.themes.light.bg_end": Setting("Light End", str, "#f0f0f0", False, "Deprecated", "Light mode gradient end", "color", deprecated=True),
            "ui.themes.light.text": Setting("Light Text", str, "#000000", False, "Deprecated", "Light mode text color", "color", deprecated=True),
            "ui.animation_styles": Setting("Animation Styles", list, ["wave", "fade", "slide", "none"], False, "Deprecated", "Enabled animations", "list", deprecated=True),
            "ui.background_styles": Setting("Bg Styles", list, ["gradient", "solid", "albumart"], False, "Deprecated", "Enabled backgrounds", "list", deprecated=True),
            "ui.minimal_mode.enabled": Setting("Minimal Mode", bool, False, False, "Deprecated", "Hide extra UI elements", "switch", deprecated=True),
            "ui.minimal_mode.hide_elements": Setting("Hidden Elements", list, ["bottom-nav"], False, "Deprecated", "Elements to hide in minimal mode", "list", deprecated=True),

            # Lyrics
            "lyrics.display.buffer_size": Setting("Buffer Size", int, 6, False, "Lyrics", "Lines to buffer", "number", min_val=1, max_val=20),
            "lyrics.display.update_interval": Setting("Update Interval", float, 0.1, False, "Lyrics", "UI refresh rate (s)", "slider", min_val=0.05, max_val=1.0),
            "lyrics.display.idle_interval": Setting("Idle Interval", float, 3.0, False, "Lyrics", "Check rate when idle (s)", "slider", min_val=1.0, max_val=30.0),
            "lyrics.display.latency_compensation": Setting("Latency Comp", float, -0.1, False, "Lyrics", "Sync offset (+early, -late)", "slider", min_val=-2.0, max_val=2.0),
            "lyrics.display.spotify_latency_compensation": Setting("Spotify Latency", float, -0.5, False, "Lyrics", "Spotify sync (+early, -late)", "slider", min_val=-2.0, max_val=2.0),
            "lyrics.display.audio_recognition_latency_compensation": Setting("Audio Rec Latency", float, 0.2, False, "Lyrics", "Audio rec sync (+early, -late)", "slider", min_val=-2.0, max_val=2.0),
            "lyrics.display.spicetify_latency_compensation": Setting("Spicetify Latency", float, 0.0, False, "Lyrics", "Spicetify sync (+early, -late)", "slider", min_val=-2.0, max_val=2.0),
            "lyrics.display.word_sync_latency_compensation": Setting("Word-Sync Latency", float, 0.0, False, "Lyrics", "Word-sync offset (+early, -late)", "slider", min_val=-2.0, max_val=2.0),
            "lyrics.display.musixmatch_word_sync_offset": Setting("Musixmatch Offset", float, 0.0, False, "Lyrics", "Musixmatch word-sync timing adjustment (s)", "slider", min_val=-1.0, max_val=1.0),
            "lyrics.display.netease_word_sync_offset": Setting("NetEase Offset", float, 0.0, False, "Lyrics", "NetEase word-sync timing adjustment (s)", "slider", min_val=-1.0, max_val=1.0),
            "lyrics.display.idle_wait_time": Setting("Idle Wait", float, 3.0, False, "Lyrics", "Time before idle (s)", "slider", min_val=1.0, max_val=10.0),
            "lyrics.display.smart_race_timeout": Setting("Race Timeout", float, 3.0, False, "Lyrics", "Provider race timeout (s)", "slider", min_val=1.0, max_val=10.0),

            # Providers
            "providers.lrclib.enabled": Setting("LRCLib", bool, True, True, "Providers", "Enable LRCLib", "switch"),
            "providers.lrclib.priority": Setting("LRCLib Priority", int, 2, False, "Providers", "Fetch priority (lower = first)", "number", min_val=1, max_val=10),
            "providers.lrclib.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number", advanced=True),
            "providers.lrclib.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number", advanced=True),
            "providers.lrclib.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number", advanced=True),

            "providers.spotify.enabled": Setting("Spotify", bool, True, True, "Providers", "Enable Spotify Lyrics", "switch"),
            "providers.spotify.priority": Setting("Spotify Priority", int, 1, False, "Providers", "Fetch priority (lower = first)", "number", min_val=1, max_val=10),
            "providers.spotify.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number", advanced=True),
            "providers.spotify.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number", advanced=True),
            "providers.spotify.token_refresh_buffer": Setting("Buffer", int, 300, False, "Providers", "Token refresh buffer (s)", "number", advanced=True),
            "providers.spotify.cache_duration": Setting("Cache", int, 3600, False, "Providers", "Cache TTL (s)", "number", advanced=True),

            "providers.qq.enabled": Setting("QQ", bool, True, True, "Providers", "Enable QQ Music", "switch"),
            "providers.qq.priority": Setting("QQ Priority", int, 4, False, "Providers", "Fetch priority (lower = first)", "number", min_val=1, max_val=10),
            "providers.qq.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number", advanced=True),
            "providers.qq.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number", advanced=True),
            "providers.qq.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number", advanced=True),

            "providers.netease.enabled": Setting("NetEase", bool, True, True, "Providers", "Enable NetEase", "switch"),
            "providers.netease.priority": Setting("NetEase Priority", int, 3, False, "Providers", "Fetch priority (lower = first)", "number", min_val=1, max_val=10),
            "providers.netease.timeout": Setting("Timeout", int, 10, False, "Providers", "Request timeout (s)", "number", advanced=True),
            "providers.netease.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number", advanced=True),
            "providers.netease.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number", advanced=True),

            "providers.musixmatch.enabled": Setting("Musixmatch", bool, True, True, "Providers", "Enable Musixmatch", "switch"),
            "providers.musixmatch.priority": Setting("Musixmatch Priority", int, 3, False, "Providers", "Fetch priority (lower = first)", "number", min_val=1, max_val=10),
            "providers.musixmatch.timeout": Setting("Timeout", int, 15, False, "Providers", "Request timeout (s)", "number", advanced=True),
            "providers.musixmatch.retries": Setting("Retries", int, 3, False, "Providers", "Max retries", "number", advanced=True),
            "providers.musixmatch.cache_duration": Setting("Cache", int, 86400, False, "Providers", "Cache TTL (s)", "number", advanced=True),

            # Storage - Deprecated (not wired up to cleanup logic)
            "storage.lyrics_db.enabled": Setting("DB Enabled", bool, True, False, "Deprecated", "Enable local DB", "switch", deprecated=True),
            "storage.lyrics_db.max_size_mb": Setting("Max DB Size", int, 100, False, "Deprecated", "Max DB size (MB)", "number", deprecated=True),
            "storage.lyrics_db.cleanup_threshold": Setting("Cleanup", float, 0.9, False, "Deprecated", "Cleanup threshold (0-1)", "slider", min_val=0.1, max_val=1.0, deprecated=True),
            "storage.lyrics_db.file_pattern": Setting("Pattern", str, "*.json", False, "Deprecated", "File pattern", deprecated=True),
            "storage.cache.enabled": Setting("Cache Enabled", bool, True, False, "Deprecated", "Enable caching", "switch", deprecated=True),
            "storage.cache.duration_days": Setting("Duration", int, 30, False, "Deprecated", "Cache duration (days)", "number", deprecated=True),
            "storage.cache.max_size_mb": Setting("Max Cache", int, 50, False, "Deprecated", "Max cache size (MB)", "number", deprecated=True),
            "storage.cache.memory_items": Setting("Mem Items", int, 100, False, "Deprecated", "Max memory items", "number", deprecated=True),

            # Notifications - Deprecated (no notification system implemented)
            "notifications.enabled": Setting("Notifications", bool, True, False, "Deprecated", "Enable notifications", "switch", deprecated=True),
            "notifications.duration": Setting("Duration", int, 3, False, "Deprecated", "Notification duration (s)", "number", deprecated=True),
            "notifications.icon_path": Setting("Icon", str, "resources/images/icon.ico", False, "Deprecated", "Icon path", deprecated=True),

            # System
            "system.windows.media_session.enabled": Setting("Win Media", bool, True, True, "System", "Enable Windows Media", "switch"),
            "system.windows.media_session.preferred": Setting("Prefer Win", bool, True, True, "System", "Prefer Windows Media", "switch"),
            "system.windows.media_session.timeout": Setting("Timeout", int, 5, False, "System", "SMTC timeout (s)", "number"),
            # Linux - Deprecated (Linux not actively supported)
            "system.linux.gsettings_enabled": Setting("GSettings", bool, True, True, "Deprecated", "Enable GSettings", "switch", deprecated=True),
            "system.linux.playerctl_required": Setting("Playerctl", bool, True, True, "Deprecated", "Require Playerctl", "switch", deprecated=True),
            
            # New Blocklist Setting
            "system.windows.app_blocklist": Setting("App Blocklist", list, ["chrome", "msedge", "firefox", "brave", "comet"], False, "System", "Apps to ignore (partial match)", "list"),
            "system.windows.paused_timeout": Setting("Paused Timeout", int, 600, False, "System", "Accept paused Windows media for N seconds (0=forever)", "number"),
            "system.spotify.paused_timeout": Setting("Spotify Paused Timeout", int, 600, False, "System", "Accept paused Spotify for N seconds (0=forever)", "number"),
            "system.spicetify.paused_timeout": Setting("Spicetify Paused Timeout", int, 600, False, "System", "Accept paused Spicetify for N seconds (0=forever)", "number"),

            # Features - Active
            "features.save_lyrics_locally": Setting("Save Lyrics Locally", bool, True, False, "Features", "Save lyrics to disk", "switch"),
            "features.parallel_provider_fetch": Setting("Parallel Fetch", bool, True, False, "Features", "Fetch from providers concurrently", "switch"),
            "features.album_art_db": Setting("Album Art Database", bool, True, False, "Features", "Enable album art database", "switch"),
            "features.word_sync_auto_switch": Setting("Word-Sync Auto-Switch", bool, True, False, "Features", "Auto-switch to provider with word-sync even if another is preferred", "switch"),
            "features.word_sync_default_enabled": Setting("Word-Sync Default On", bool, True, False, "Features", "Enable word-sync by default (frontend can still toggle)", "switch"),
            "features.spicetify_database": Setting("Spicetify Database", bool, True, False, "Features", "Cache audio analysis for waveform/spectrum", "switch"),
            
            # Features - Deprecated (not wired up)
            "features.minimal_ui": Setting("Minimal UI", bool, False, False, "Deprecated", "Enable minimal mode", "switch", deprecated=True),
            "features.show_lyrics_source": Setting("Show Source", bool, True, False, "Deprecated", "Show provider name", "switch", deprecated=True),
            "features.provider_stats": Setting("Stats", bool, False, False, "Deprecated", "Track provider stats", "switch", deprecated=True),
            "features.auto_theme": Setting("Auto Theme", bool, True, False, "Deprecated", "Auto-switch theme", "switch", deprecated=True),
            "features.album_art_colors": Setting("Art Colors", bool, True, False, "Deprecated", "Use album art colors", "switch", deprecated=True),

            # Media Source
            "media_source.spotify.enabled": Setting("Spotify Source", bool, True, True, "Media", "Enable Spotify source", "switch"),
            "media_source.spotify.priority": Setting("Priority", int, 2, False, "Media", "Source priority", "number"),
            "media_source.windows_media.enabled": Setting("Win Source", bool, True, True, "Media", "Enable Windows source", "switch"),
            "media_source.windows_media.priority": Setting("Priority", int, 1, False, "Media", "Source priority", "number"),
            "media_source.gnome.enabled": Setting("Gnome Source", bool, False, True, "Media", "Enable Gnome source", "switch"),
            "media_source.gnome.priority": Setting("Priority", int, 2, False, "Media", "Source priority", "number"),
            "media_source.spicetify.enabled": Setting("Spicetify Source", bool, True, True, "Media", "Enable Spicetify bridge (Spotify Desktop)", "switch"),
            "media_source.spicetify.priority": Setting("Priority", int, 0, False, "Media", "Source priority (0 = highest)", "number"),
            
            # Spotify API
            "spotify.redirect_uri": Setting("Redirect URI", str, "http://127.0.0.1:9012/callback", True, "Spotify API", "Callback URL"),
            "spotify.cache.metadata_ttl": Setting("Metadata TTL", float, 2.0, False, "Spotify API", "Metadata cache (s)", "number"),
            "spotify.cache.enabled": Setting("Cache Enabled", bool, True, False, "Spotify API", "Enable API cache", "switch"),
            "spotify.polling.fast_interval": Setting("Fast Poll Interval", float, 2.0, False, "Spotify API", "Spotify-only mode polling (s)", "slider", min_val=0.5, max_val=10.0),
            "spotify.polling.slow_interval": Setting("Slow Poll Interval", float, 6.0, False, "Spotify API", "Hybrid/idle mode polling (s)", "slider", min_val=1.0, max_val=30.0),

            
            # Album Art
            "album_art.timeout": Setting("Timeout", int, 5, False, "Album Art", "Request timeout (s)", "number", min_val=1, max_val=30),
            "album_art.retries": Setting("Retries", int, 2, False, "Album Art", "Max retries", "number", min_val=0, max_val=5),
            "album_art.enable_itunes": Setting("iTunes", bool, True, False, "Album Art", "Enable iTunes source", "switch"),
            "album_art.enable_lastfm": Setting("Last.fm", bool, True, False, "Album Art", "Enable Last.fm source", "switch"),
            "album_art.enable_spotify_enhanced": Setting("Spotify Enhanced", bool, True, False, "Album Art", "Try to enhance Spotify URLs", "switch"),
            "album_art.min_resolution": Setting("Min Resolution", int, 3000, False, "Album Art", "Preferred resolution (px)", "number", min_val=640, max_val=3000),
            
            # Artist Image
            "artist_image.timeout": Setting("Timeout", int, 5, False, "Artist Image", "Request timeout (s)", "number"),
            "artist_image.enable_wikipedia": Setting("Wikipedia", bool, False, False, "Artist Image", "Enable Wikipedia/Wikimedia", "switch"),
            "artist_image.enable_fanart_albumcover": Setting("FanArt Album Covers", bool, True, False, "Artist Image", "Fetch FanArt.tv album covers", "switch"),

            # Visual Mode
            "visual_mode.enabled": Setting("Visual Mode", bool, True, False, "Visual Mode", "Enable visual mode for instrumentals", "switch"),
            "visual_mode.delay_seconds": Setting("Delay", int, 6, False, "Visual Mode", "Delay before hiding lyrics (s)", "slider", min_val=1, max_val=60),
            "visual_mode.auto_sharp": Setting("Auto Sharp", bool, True, False, "Visual Mode", "Auto-switch to sharp mode in visual mode", "switch"),
            "visual_mode.slideshow.enabled": Setting("Slideshow", bool, False, False, "Visual Mode", "Enable slideshow when no music", "switch"),
            "visual_mode.slideshow.interval_seconds": Setting("Slideshow Speed", int, 8, False, "Visual Mode", "Seconds per image", "slider", min_val=3, max_val=3600),

            # Audio Recognition (Reaper Integration)
            "audio_recognition.enabled": Setting("Audio Recognition", bool, False, False, "Audio Recognition", "Enable audio fingerprinting", "switch"),
            "audio_recognition.reaper_auto_detect": Setting("Reaper Auto-Detect", bool, False, False, "Audio Recognition", "Auto-start when Reaper detected", "switch"),
            "audio_recognition.device_id": Setting("Device ID", int, None, False, "Audio Recognition", "Audio device ID (blank = auto)", "number"),
            "audio_recognition.device_name": Setting("Device Name", str, "", False, "Audio Recognition", "Preferred device name"),
            "audio_recognition.capture_duration": Setting("Capture Duration", float, 5.0, False, "Audio Recognition", "Audio capture length (s)", "slider", min_val=3.0, max_val=60.0),
            "audio_recognition.recognition_interval": Setting("Recognition Interval", float, 5.0, False, "Audio Recognition", "Time between recognitions (s)", "slider", min_val=3.0, max_val=30.0),
            "audio_recognition.latency_offset": Setting("Latency Offset", float, 0.0, False, "Audio Recognition", "Manual latency adjustment (s)", "slider", min_val=-5.0, max_val=5.0),
            "audio_recognition.silence_threshold": Setting("Silence Threshold", int, 500, False, "Audio Recognition", "Min amplitude to detect audio", "slider", min_val=50, max_val=2000),

            # HTTPS Settings (for browser microphone access)
            "server.https.enabled": Setting("HTTPS Enabled", bool, True, True, "HTTPS", "Enable HTTPS (required for browser mic)", "switch"),
            "server.https.port": Setting("HTTPS Port", int, 9013, True, "HTTPS", "HTTPS port (0 = same as HTTP, >0 = dual-stack)", "number"),
            "server.https.auto_generate": Setting("Auto Generate Cert", bool, True, False, "HTTPS", "Auto-generate self-signed certificate", "switch"),
            "server.https.cert_file": Setting("Cert File", str, "certs/server.crt", True, "HTTPS", "SSL certificate file path"),
            "server.https.key_file": Setting("Key File", str, "certs/server.key", True, "HTTPS", "SSL private key file path"),
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
                logger.error(f"Failed to load settings.json: {e} - resetting to defaults")
                # FIX: Backup corrupted file and reset
                if SETTINGS_FILE.exists():
                    backup_path = SETTINGS_FILE.with_suffix('.json.corrupted')
                    try:
                        shutil.copy2(SETTINGS_FILE, backup_path)
                        logger.info(f"Backed up corrupted settings to {backup_path}")
                    except Exception:
                        pass
                self.save_to_config()  # Save defaults
        else:
            # FIX: Create default settings file on first run
            logger.info(f"Creating default settings file at {SETTINGS_FILE}")
            self.save_to_config()

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
            # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
            # This prevents race conditions when multiple settings updates happen simultaneously
            temp_filename = f"settings_{uuid.uuid4().hex}.json.tmp"
            temp_path = SETTINGS_FILE.parent / temp_filename
            
            with open(temp_path, 'w') as f:
                # FIX: Sanitize lists before saving to prevent corruption
                sanitized = {}
                for key, val in self._settings.items():
                    defin = self._definitions.get(key)
                    if defin and defin.type == list:
                        if isinstance(val, str):
                            logger.warning(f"List setting '{key}' was string, restoring default")
                            val = defin.default
                        elif not isinstance(val, list):
                            logger.warning(f"List setting '{key}' invalid type, restoring default")
                            val = defin.default
                    sanitized[key] = val
                json.dump(sanitized, f, indent=4, sort_keys=True)
            
            # Atomic replace (works on both Windows and Unix)
            if SETTINGS_FILE.exists():
                try:
                    os.remove(SETTINGS_FILE)
                except:
                    pass
            os.replace(temp_path, SETTINGS_FILE)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            # Clean up temp file if it exists
            if 'temp_path' in locals() and temp_path.exists():
                try:
                    os.remove(temp_path)
                except:
                    pass

    def get_all(self) -> Dict:
        """Return formatted settings for UI"""
        result = {}
        for key, val in self._settings.items():
            # Skip keys not in definitions (e.g., loaded from JSON but not defined in schema)
            defin = self._definitions.get(key)
            if not defin:
                continue
                
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
                "max": defin.max_val,
                "deprecated": defin.deprecated
            }
        return result

    def reset_to_defaults(self):
        if SETTINGS_FILE.exists():
            os.remove(SETTINGS_FILE)
        self.load_settings()

settings = SettingsManager()
