"""
System Utils Package - Backward Compatible Facade

This package refactors the monolithic system_utils.py into focused modules
while maintaining full backward compatibility through re-exports.

External code can continue using:
    from system_utils import get_current_song_meta_data
    from system_utils import _art_update_lock

The internal structure is:
    state.py      - Shared locks, caches, trackers
    helpers.py    - Pure utility functions
    image.py      - Image I/O and color extraction
    gnome.py      - GNOME/Linux metadata
    album_art.py  - Album art database
    artist_image.py - Artist image database
    windows.py    - Windows Media Session
    spotify.py    - Spotify metadata
    metadata.py   - Main orchestrator
"""

# ============================================================================
# Re-export all public APIs for backward compatibility
# ============================================================================

# --- Level 0: State (locks, caches, trackers) ---
from .state import (
    _art_update_lock,
    _art_update_thread_lock,
    _meta_data_lock,
    _art_download_semaphore,
    _artist_download_semaphore,
    _background_tasks,
    _running_art_upgrade_tasks,
    _db_checked_tracks,
    _MAX_DB_CHECKED_SIZE,
    _spotify_download_tracker,
    _artist_download_tracker,
    _artist_image_log_throttle,
    _ARTIST_IMAGE_LOG_THROTTLE_SECONDS,
    _artist_db_check_cache,
    _artist_image_provider,
    _metadata_fetch_counters,
    _last_state_log_time,
    STATE_LOG_INTERVAL,
    _last_windows_track_id,
    _last_windows_app_id,
    _album_art_metadata_cache,
    _MAX_METADATA_CACHE_SIZE,
    _discovery_cache,
    _MAX_DISCOVERY_CACHE_SIZE,
    _artist_image_load_cache,
    _MAX_ARTIST_IMAGE_CACHE_SIZE,
    _ARTIST_IMAGE_CACHE_TTL,
    _color_cache,
    _MAX_CACHE_SIZE,
    _metadata_file_locks,
    _metadata_locks_lock,
)

# --- Level 1: Helpers (pure utilities) ---
from .helpers import (
    create_tracked_task,
    _cleanup_artist_image_log_throttle,
    _remove_text_inside_parentheses_and_brackets,
    _normalize_track_id,
    sanitize_folder_name,
    _log_app_state,
)

# --- Level 1: Image (image I/O and color extraction) ---
from .image import (
    get_image_extension,
    determine_image_extension,
    save_image_original,
    get_cached_art_path,
    get_cached_art_mtime,
    cleanup_old_art,
    extract_dominant_colors_sync,
    extract_dominant_colors,
)

# --- Level 2: Album Art Database ---
from .album_art import (
    get_album_db_folder,
    save_album_db_metadata,
    _download_and_save_sync,
    ensure_album_art_db,
    discover_custom_images,
    load_album_art_from_db,
)

# --- Level 3: Artist Image Database ---
from .artist_image import (
    load_artist_image_from_db,
    clear_artist_image_cache,
    _get_artist_image_fallback,
    ensure_artist_image_db,
)

# --- Level 5: Main Orchestrator ---
from .metadata import (
    get_current_song_meta_data,
)

# --- Level 6: Session Config (runtime overrides) ---
from .session_config import (
    set_session_override,
    get_session_override,
    clear_session_overrides,
    has_session_overrides,
    get_audio_config_with_overrides,
    get_effective_value,
    get_active_overrides,
)

# Note: Platform-specific functions (windows.py, spotify.py, gnome.py) are
# intentionally NOT re-exported at the package level. They are internal
# implementation details called by get_current_song_meta_data.

# ============================================================================
# __all__ for explicit public API
# ============================================================================
__all__ = [
    # State
    '_art_update_lock',
    '_art_update_thread_lock',
    '_meta_data_lock',
    '_art_download_semaphore',
    '_artist_download_semaphore',
    '_background_tasks',
    '_running_art_upgrade_tasks',
    '_db_checked_tracks',
    '_MAX_DB_CHECKED_SIZE',
    '_spotify_download_tracker',
    '_artist_download_tracker',
    '_artist_image_log_throttle',
    '_ARTIST_IMAGE_LOG_THROTTLE_SECONDS',
    '_artist_db_check_cache',
    '_artist_image_provider',
    '_metadata_fetch_counters',
    '_last_state_log_time',
    'STATE_LOG_INTERVAL',
    '_last_windows_track_id',
    '_last_windows_app_id',
    '_album_art_metadata_cache',
    '_MAX_METADATA_CACHE_SIZE',
    '_discovery_cache',
    '_MAX_DISCOVERY_CACHE_SIZE',
    '_artist_image_load_cache',
    '_MAX_ARTIST_IMAGE_CACHE_SIZE',
    '_ARTIST_IMAGE_CACHE_TTL',
    '_color_cache',
    '_MAX_CACHE_SIZE',
    '_metadata_file_locks',
    '_metadata_locks_lock',
    
    # Helpers
    'create_tracked_task',
    '_cleanup_artist_image_log_throttle',
    '_remove_text_inside_parentheses_and_brackets',
    '_normalize_track_id',
    'sanitize_folder_name',
    '_log_app_state',
    
    # Image
    'get_image_extension',
    'determine_image_extension',
    'save_image_original',
    'get_cached_art_path',
    'get_cached_art_mtime',
    'cleanup_old_art',
    'extract_dominant_colors_sync',
    'extract_dominant_colors',
    
    # Album Art
    'get_album_db_folder',
    'save_album_db_metadata',
    '_download_and_save_sync',
    'ensure_album_art_db',
    'discover_custom_images',
    'load_album_art_from_db',
    
    # Artist Image
    'load_artist_image_from_db',
    'clear_artist_image_cache',
    '_get_artist_image_fallback',
    'ensure_artist_image_db',
    
    # Metadata
    'get_current_song_meta_data',
    
    # Session Config
    'set_session_override',
    'get_session_override',
    'clear_session_overrides',
    'has_session_overrides',
    'get_audio_config_with_overrides',
    'get_effective_value',
    'get_active_overrides',
]
