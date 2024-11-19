"""
SyncLyrics Settings Manager
Handles dynamic configuration management with support for runtime and restart-required settings.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, asdict
from logging_config import get_logger

logger = get_logger(__name__)

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
        """Validate and convert a value to the correct type"""
        try:
            if self.type == bool and isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return self.type(value)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid value for {self.name}: {value}")
            return self.default

class SettingsManager:
    """Manages application settings with support for runtime updates"""
    
    def __init__(self):
        self._settings: Dict[str, Any] = {}
        self._config_path = Path(__file__).parent / "config.py"
        self._backup_dir = Path(__file__).parent / "config_backups"
        self._backup_dir.mkdir(exist_ok=True)
        
        self._definitions = {
            # Debug Settings
            "debug.enabled": Setting("Debug Mode", bool, False, True, "Debug", "Enable debug mode"),
            "debug.log_file": Setting("Log File", str, "synclyrics.log", True, "Debug", "Log file path"),
            "debug.log_level": Setting("Log Level", str, "WARNING", True, "Debug", "Logging verbosity level"),
            "debug.log_providers": Setting("Log Providers", bool, True, False, "Debug", "Enable provider logging"),
            "debug.log_polling": Setting("Log Polling", bool, True, False, "Debug", "Enable polling logging"),
            "debug.log_to_console": Setting("Log to Console", bool, True, False, "Debug", "Output logs to console"),
            "debug.log_detailed": Setting("Detailed Logging", bool, False, False, "Debug", "Enable detailed logging"),
            "debug.performance_logging": Setting("Performance Logging", bool, False, False, "Debug", "Enable performance logging"),
            "debug.log_rotation.max_bytes": Setting("Max Log Size", int, 10*1024*1024, False, "Debug", "Maximum log file size in bytes"),
            "debug.log_rotation.backup_count": Setting("Log Backups", int, 5, False, "Debug", "Number of log backup files to keep"),

            # Server Settings
            "server.port": Setting("Port", int, 9012, True, "Server", "Server port number"),
            "server.host": Setting("Host", str, "0.0.0.0", True, "Server", "Server host address"),
            "server.secret_key": Setting("Secret Key", str, "your-secret-key-here", True, "Server", "Secret key for session management"),
            "server.debug": Setting("Debug Mode", bool, False, True, "Server", "Enable server debug mode"),

            # UI Settings
            "ui.themes.default.bg_start": Setting("Default Theme Start", str, "#24273a", False, "UI", "Default theme background start color"),
            "ui.themes.default.bg_end": Setting("Default Theme End", str, "#363b54", False, "UI", "Default theme background end color"),
            "ui.themes.default.text": Setting("Default Theme Text", str, "#ffffff", False, "UI", "Default theme text color"),
            "ui.themes.dark.bg_start": Setting("Dark Theme Start", str, "#1c1c1c", False, "UI", "Dark theme background start color"),
            "ui.themes.dark.bg_end": Setting("Dark Theme End", str, "#2c2c2c", False, "UI", "Dark theme background end color"),
            "ui.themes.dark.text": Setting("Dark Theme Text", str, "#ffffff", False, "UI", "Dark theme text color"),
            "ui.themes.light.bg_start": Setting("Light Theme Start", str, "#ffffff", False, "UI", "Light theme background start color"),
            "ui.themes.light.bg_end": Setting("Light Theme End", str, "#f0f0f0", False, "UI", "Light theme background end color"),
            "ui.themes.light.text": Setting("Light Theme Text", str, "#000000", False, "UI", "Light theme text color"),
            "ui.animation_styles": Setting("Animation Styles", list, ["wave", "fade", "slide", "none"], False, "UI", "Available animation styles"),
            "ui.background_styles": Setting("Background Styles", list, ["gradient", "solid", "albumart"], False, "UI", "Available background styles"),
            "ui.minimal_mode.enabled": Setting("Minimal Mode", bool, True, False, "UI", "Enable minimal UI mode"),
            "ui.minimal_mode.hide_elements": Setting("Hidden Elements", list, ["bottom-nav", "provider-info", "minimal-toggle"], False, "UI", "UI elements to hide in minimal mode"),

            # Lyrics Settings
            "lyrics.display.buffer_size": Setting("Buffer Size", int, 6, False, "Lyrics", "Number of lyrics lines to buffer"),
            "lyrics.display.update_interval": Setting("Update Interval", float, 0.1, False, "Lyrics", "Time between lyric updates (seconds)"),
            "lyrics.display.idle_interval": Setting("Idle Interval", float, 5.0, False, "Lyrics", "Polling interval when idle (seconds)"),
            "lyrics.display.latency_compensation": Setting("Latency Compensation", float, 0.17, False, "Lyrics", "Adjust timing of lyrics (seconds)"),
            "lyrics.display.idle_wait_time": Setting("Idle Wait Time", float, 3.0, False, "Lyrics", "Wait time before switching to idle mode"),

            # Provider Settings
            "providers.lrclib.enabled": Setting("Enable LRCLib", bool, True, True, "Providers", "Enable LRCLib lyrics provider"),
            "providers.lrclib.priority": Setting("LRCLib Priority", int, 1, False, "Providers", "Priority of LRCLib provider"),
            "providers.lrclib.timeout": Setting("LRCLib Timeout", int, 10, False, "Providers", "Request timeout in seconds"),
            "providers.lrclib.retries": Setting("LRCLib Retries", int, 3, False, "Providers", "Number of retry attempts"),
            "providers.lrclib.cache_duration": Setting("LRCLib Cache Duration", int, 86400, False, "Providers", "Cache duration in seconds"),

            "providers.spotify.enabled": Setting("Enable Spotify", bool, True, True, "Providers", "Enable Spotify lyrics provider"),
            "providers.spotify.priority": Setting("Spotify Priority", int, 2, False, "Providers", "Priority of Spotify provider"),
            "providers.spotify.timeout": Setting("Spotify Timeout", int, 10, False, "Providers", "Request timeout in seconds"),
            "providers.spotify.retries": Setting("Spotify Retries", int, 3, False, "Providers", "Number of retry attempts"),
            "providers.spotify.token_refresh_buffer": Setting("Token Refresh Buffer", int, 300, False, "Providers", "Buffer time before token refresh"),
            "providers.spotify.cache_duration": Setting("Spotify Cache Duration", int, 3600, False, "Providers", "Cache duration in seconds"),

            "providers.qq.enabled": Setting("Enable QQ Music", bool, True, True, "Providers", "Enable QQ Music lyrics provider"),
            "providers.qq.priority": Setting("QQ Priority", int, 4, False, "Providers", "Priority of QQ Music provider"),
            "providers.qq.timeout": Setting("QQ Timeout", int, 10, False, "Providers", "Request timeout in seconds"),
            "providers.qq.retries": Setting("QQ Retries", int, 3, False, "Providers", "Number of retry attempts"),
            "providers.qq.cache_duration": Setting("QQ Cache Duration", int, 86400, False, "Providers", "Cache duration in seconds"),

            "providers.netease.enabled": Setting("Enable Netease", bool, True, True, "Providers", "Enable Netease lyrics provider"),
            "providers.netease.priority": Setting("Netease Priority", int, 3, False, "Providers", "Priority of Netease provider"),
            "providers.netease.timeout": Setting("Netease Timeout", int, 10, False, "Providers", "Request timeout in seconds"),
            "providers.netease.retries": Setting("Netease Retries", int, 3, False, "Providers", "Number of retry attempts"),
            "providers.netease.cache_duration": Setting("Netease Cache Duration", int, 86400, False, "Providers", "Cache duration in seconds"),

            # Storage Settings
            "storage.lyrics_db.enabled": Setting("Enable Lyrics DB", bool, True, False, "Storage", "Enable lyrics database"),
            "storage.lyrics_db.max_size_mb": Setting("Max DB Size", int, 100, False, "Storage", "Maximum lyrics database size in MB"),
            "storage.lyrics_db.cleanup_threshold": Setting("Cleanup Threshold", float, 0.9, False, "Storage", "Database cleanup threshold"),
            "storage.lyrics_db.file_pattern": Setting("DB File Pattern", str, "*.json", False, "Storage", "Database file pattern"),

            "storage.cache.enabled": Setting("Enable Cache", bool, True, False, "Storage", "Enable caching"),
            "storage.cache.duration_days": Setting("Cache Duration", int, 30, False, "Storage", "Cache duration in days"),
            "storage.cache.max_size_mb": Setting("Max Cache Size", int, 50, False, "Storage", "Maximum cache size in MB"),
            "storage.cache.memory_items": Setting("Memory Cache Items", int, 100, False, "Storage", "Maximum items in memory cache"),

            # Notification Settings
            "notifications.enabled": Setting("Enable Notifications", bool, True, False, "Notifications", "Enable notifications"),
            "notifications.duration": Setting("Duration", int, 3, False, "Notifications", "Notification duration in seconds"),
            "notifications.icon_path": Setting("Icon Path", str, str(Path(__file__).parent / "resources" / "images" / "icon.ico"), False, "Notifications", "Path to notification icon"),

            # System Settings
            "system.windows.media_session.enabled": Setting("Windows Media Session", bool, True, True, "System", "Enable Windows media session"),
            "system.windows.media_session.preferred": Setting("Prefer Media Session", bool, True, True, "System", "Prefer Windows media session"),
            "system.windows.media_session.timeout": Setting("Media Session Timeout", int, 5, False, "System", "Media session timeout in seconds"),

            "system.linux.gsettings_enabled": Setting("Linux GSettings", bool, True, True, "System", "Enable Linux GSettings"),
            "system.linux.playerctl_required": Setting("Linux Playerctl", bool, True, True, "System", "Require Playerctl on Linux"),

            # Feature Flags
            "features.minimal_ui": Setting("Minimal UI", bool, True, False, "Features", "Enable minimal UI mode"),
            "features.save_lyrics_locally": Setting("Save Lyrics", bool, True, False, "Features", "Save found lyrics to database"),
            "features.show_lyrics_source": Setting("Show Source", bool, True, False, "Features", "Show which provider found the lyrics"),
            "features.parallel_provider_fetch": Setting("Parallel Fetch", bool, True, False, "Features", "Try multiple providers simultaneously"),
            "features.provider_stats": Setting("Provider Stats", bool, False, False, "Features", "Track provider success rates"),
            "features.auto_theme": Setting("Auto Theme", bool, True, False, "Features", "Auto-detect system theme"),
            "features.album_art_colors": Setting("Album Art Colors", bool, True, False, "Features", "Extract colors from album art if available"),

            # Media Source Settings
            "media_source.spotify.enabled": Setting("Enable Spotify Source", bool, True, True, "Media Sources", "Enable Spotify as media source"),
            "media_source.spotify.priority": Setting("Spotify Source Priority", int, 2, False, "Media Sources", "Priority for Spotify media source"),
            "media_source.windows_media.enabled": Setting("Enable Windows Media", bool, True, True, "Media Sources", "Enable Windows Media as source"),
            "media_source.windows_media.priority": Setting("Windows Media Priority", int, 1, False, "Media Sources", "Priority for Windows Media source"),
            "media_source.gnome.enabled": Setting("Enable Gnome", bool, False, True, "Media Sources", "Enable Gnome as media source"),
            "media_source.gnome.priority": Setting("Gnome Priority", int, 2, False, "Media Sources", "Priority for Gnome media source"),

            # Spotify API Settings
            "spotify.client_id": Setting("Spotify Client ID", str, "", True, "Spotify API", "Spotify API Client ID"),
            "spotify.client_secret": Setting("Spotify Client Secret", str, "", True, "Spotify API", "Spotify API Client Secret"),
            "spotify.redirect_uri": Setting("Spotify Redirect URI", str, "http://localhost:9012/callback", True, "Spotify API", "Spotify OAuth redirect URI"),
            "spotify.base_url": Setting("Spotify Lyrics API URL", str, "https://spotify-lyrics-api-azure.vercel.app", True, "Spotify API", "Spotify lyrics API server URL"),
            "spotify.cache.metadata_ttl": Setting("Metadata Cache TTL", float, 3.0, False, "Spotify API", "Time to live for metadata cache in seconds"),
            "spotify.cache.enabled": Setting("Enable Cache", bool, True, False, "Spotify API", "Enable/disable Spotify caching"),

            # Base Paths
            "paths.root_dir": Setting("Root Directory", str, str(Path(__file__).parent), True, "Paths", "Application root directory"),
            "paths.resources_dir": Setting("Resources Directory", str, "resources", True, "Paths", "Resources directory path"),
            "paths.database_dir": Setting("Database Directory", str, "lyrics_database", True, "Paths", "Lyrics database directory"),
            "paths.cache_dir": Setting("Cache Directory", str, "cache", True, "Paths", "Cache directory path"),
        }
        
    def _create_backup(self) -> Path:
        """Create a backup of the current config.py"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self._backup_dir / f"config_{timestamp}.py"
        try:
            shutil.copy2(self._config_path, backup_path)
            logger.info(f"Created config backup at {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create config backup: {e}")
            raise

    def _restore_backup(self, backup_path: Path) -> None:
        """Restore config.py from a backup"""
        try:
            shutil.copy2(backup_path, self._config_path)
            logger.info(f"Restored config from backup {backup_path}")
        except Exception as e:
            logger.error(f"Failed to restore config from backup: {e}")
            raise

    def load_settings(self) -> None:
        """Load settings from config.py into memory"""
        try:
            # Import config dynamically to get current values
            import config
            
            # Load settings from config module
            for key, setting in self._definitions.items():
                # Split only on first dot to handle nested settings
                category = key.split('.', 1)[0]
                if hasattr(config, category.upper()):
                    category_dict = getattr(config, category.upper())
                    
                    # Handle nested settings (e.g., providers.spotify.enabled)
                    value = category_dict
                    for part in key.split('.')[1:]:
                        if isinstance(value, dict) and part in value:
                            value = value[part]
                        else:
                            value = setting.default
                            break
                            
                    self._settings[key] = setting.validate_and_convert(value)
                else:
                    self._settings[key] = setting.default
                    
        except ImportError:
            logger.warning("Could not import config.py, using default settings")
            self._settings = {key: setting.default for key, setting in self._definitions.items()}

    def get(self, key: str) -> Any:
        """Get a setting value by key"""
        if key not in self._definitions:
            raise KeyError(f"Unknown setting: {key}")
        return self._settings.get(key, self._definitions[key].default)

    def set(self, key: str, value: Any) -> bool:
        """Set a setting value and return whether restart is required"""
        if key not in self._definitions:
            raise KeyError(f"Unknown setting: {key}")
            
        setting = self._definitions[key]
        converted_value = setting.validate_and_convert(value)
        self._settings[key] = converted_value
        
        return setting.requires_restart

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Get all settings organized by category"""
        result = {}
        for key, value in self._settings.items():
            setting = self._definitions[key]
            category = setting.category or "Misc"
            
            if category not in result:
                result[category] = {}
                
            result[category][key] = {
                "value": value,
                "default": setting.default,
                "name": setting.name,
                "description": setting.description,
                "requires_restart": setting.requires_restart,
                "type": setting.type.__name__
            }
        return result

    def save_to_config(self) -> None:
        """Save current settings by updating config.py without overwriting other settings"""
        try:
            # Create backup before making changes
            backup_path = self._create_backup()
            logger.debug(f"Created backup at {backup_path}")
            logger.debug(f"Current settings to save: {self._settings}")
            
            import config
            config_path = Path(__file__).parent / "config.py"
            
            # Read the entire file
            try:
                with open(config_path, "r", encoding='utf-8') as f:
                    content = f.read()
                logger.debug(f"Successfully read config.py")
            except Exception as e:
                logger.error(f"Error reading config.py: {str(e)}")
                self._restore_backup(backup_path)
                raise
            
            # Split content into sections
            updated_sections = []
            current_section = []
            in_config_section = False
            section_updated = False
            
            for line in content.split('\n'):
                # If we find a new section marker
                if line.strip().startswith('# ') and line.strip().endswith('Configuration'):
                    if current_section:
                        updated_sections.extend(current_section)
                        current_section = []
                    
                    section_name = line.strip('# ').split()[0]
                    if section_name in ['UI', 'LYRICS', 'SERVER', 'DEBUG', 'PROVIDERS', 'FEATURES', 'STORAGE', 'NOTIFICATIONS', 'SYSTEM']:
                        logger.debug(f"Processing section: {section_name}")
                        in_config_section = True
                        current_section = [line]  # Start new section with header
                        section_updated = False
                        
                        # Get the updated configuration
                        config_name = section_name.upper()
                        settings_dict = {}
                        prefix = config_name.lower() + '.'
                        
                        # Log relevant settings for this section
                        relevant_settings = {k: v for k, v in self._settings.items() if k.startswith(prefix)}
                        logger.debug(f"Settings for {config_name}: {relevant_settings}")
                        
                        if relevant_settings:
                            # Collect all settings for this section
                            for key, value in relevant_settings.items():
                                parts = key[len(prefix):].split('.')
                                current = settings_dict
                                for part in parts[:-1]:
                                    if part not in current:
                                        current[part] = {}
                                    current = current[part]
                                # Handle special types
                                if isinstance(value, (list, dict)):
                                    current[parts[-1]] = value
                                elif isinstance(value, bool):
                                    current[parts[-1]] = bool(value)
                                else:
                                    current[parts[-1]] = value
                                logger.debug(f"Updated {key} to {value} (type: {type(value)})")
                                section_updated = True
                        
                        # If we have updates for this section
                        if settings_dict:
                            # Format the dictionary with proper Python syntax for lists
                            def format_value(v):
                                if isinstance(v, list):
                                    return repr(v)
                                elif isinstance(v, dict):
                                    return '{\n' + ',\n'.join(f'        "{k}": {format_value(val)}' for k, val in v.items()) + '\n    }'
                                elif isinstance(v, bool):
                                    return str(v)
                                elif isinstance(v, (int, float)):
                                    return str(v)
                                else:
                                    return f'"{v}"'
                            
                            formatted_dict = '{\n' + ',\n'.join(f'    "{k}": {format_value(v)}' for k, v in settings_dict.items()) + '\n}'
                            current_section.extend([
                                f"{config_name} = {formatted_dict}",
                                ""
                            ])
                            logger.debug(f"Writing section {config_name} with content: {formatted_dict}")
                    else:
                        in_config_section = False
                        current_section = [line]
                elif line.strip().startswith(('UI', 'LYRICS', 'SERVER', 'DEBUG', 'PROVIDERS', 'FEATURES', 'STORAGE', 'NOTIFICATIONS', 'SYSTEM')):
                    # Skip the original configuration line if we've updated this section
                    if not section_updated:
                        current_section.append(line)
                else:
                    current_section.append(line)
            
            # Add the last section
            if current_section:
                updated_sections.extend(current_section)
            
            # Write the updated content back
            try:
                content = '\n'.join(updated_sections)
                with open(config_path, "w", encoding='utf-8') as f:
                    f.write(content)
                logger.info("Settings saved successfully while preserving config.py structure")
                logger.debug("Final config content length: " + str(len(content)))
            except Exception as e:
                logger.error(f"Error writing to config.py: {str(e)}")
                self._restore_backup(backup_path)
                raise
            
        except Exception as e:
            logger.error(f"Error saving settings to config.py: {str(e)}", exc_info=True)
            raise

# Global settings instance
settings = SettingsManager()
