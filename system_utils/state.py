"""
Shared State Module for system_utils package.
Contains all singleton locks, caches, trackers, and constants.

CRITICAL: This module must be imported first by all other modules.
It imports NOTHING from the system_utils package to prevent circular imports.
"""
from __future__ import annotations
import asyncio
import threading
from typing import Optional, Dict, Any
from collections import OrderedDict

import config
from logging_config import get_logger

# Initialize Logger for this module
logger = get_logger(__name__)

# ==========================================
# CONSTANTS
# ==========================================

# Intervals (from config)
ACTIVE_INTERVAL = config.LYRICS["display"]["update_interval"]
IDLE_INTERVAL = config.LYRICS["display"]["idle_interval"]
IDLE_WAIT_TIME = config.LYRICS["display"]["idle_wait_time"]

# State logging interval
STATE_LOG_INTERVAL = 300  # Log app state every 300 seconds (5 minutes)

# Cache size limits
_MAX_CACHE_SIZE = 50
_MAX_METADATA_CACHE_SIZE = 50
_MAX_DISCOVERY_CACHE_SIZE = 50
_MAX_ARTIST_IMAGE_CACHE_SIZE = 50
_MAX_DB_CHECKED_SIZE = 100

# Cache TTLs
_ARTIST_IMAGE_CACHE_TTL = 15  # Cache for 15 seconds

# Throttle intervals
_ARTIST_IMAGE_LOG_THROTTLE_SECONDS = 60  # Log at most once per minute per artist

# ==========================================
# ASYNCIO LOCKS (Singleton - must not be duplicated)
# ==========================================

# Global lock to prevent concurrent album art updates (prevents flicker)
_art_update_lock = asyncio.Lock()

# Global lock to prevent race conditions during metadata updates
_meta_data_lock = asyncio.Lock()

# Lock for Spicetify shared state access (prevents torn reads during updates)
_spicetify_state_lock = asyncio.Lock()

# Semaphore to limit concurrent background downloads
# Prevents network saturation if user skips many tracks quickly
_art_download_semaphore = asyncio.Semaphore(2)  # For album art downloads
_artist_download_semaphore = asyncio.Semaphore(2)  # For artist image downloads (separate to prevent deadlock)

# ==========================================
# THREADING LOCKS (Singleton - must not be duplicated)
# ==========================================

# For sync operations in thread executors
_art_update_thread_lock = threading.Lock()

# Per-folder locks for metadata.json file operations (prevents Windows file locking errors)
# Each album folder gets its own lock, allowing parallel writes to different albums
_metadata_file_locks: Dict[str, threading.Lock] = {}
_metadata_locks_lock = threading.Lock()  # Protects the lock dictionary itself

# ==========================================
# TASK TRACKING
# ==========================================

# Global set to track background tasks and prevent garbage collection
_background_tasks: set = set()

# Track running background art upgrade tasks to prevent duplicates
_running_art_upgrade_tasks: Dict[str, Any] = {}  # Key: track_id, Value: asyncio.Task

# ==========================================
# DOWNLOAD TRACKERS (Prevent duplicates)
# ==========================================

# Track in-progress downloads to prevent polling loop from spawning duplicates
_spotify_download_tracker: set = set()

# Track in-progress artist image downloads to prevent race conditions
_artist_download_tracker: set = set()

# Track which songs we've already checked/populated the DB for to prevent infinite loops
# Using OrderedDict for FIFO eviction (oldest entries removed first)
_db_checked_tracks: OrderedDict = OrderedDict()

# ==========================================
# CACHES
# ==========================================

# Cache for color extraction to avoid re-processing the same image
# Key: file_path, Value: (mtime, [color1, color2])
_color_cache: Dict[str, tuple] = {}

# Cache for album art metadata.json files
# Key: file_path (str), Value: (mtime, metadata_dict)
# Uses file modification time to automatically invalidate when file changes
_album_art_metadata_cache: Dict[str, tuple] = {}

# Cache for custom image discovery results
# Key: folder_path (str), Value: (folder_mtime, discovered_count)
# Uses folder modification time to avoid re-scanning on every metadata load
_discovery_cache: Dict[str, tuple] = {}

# Cache for load_artist_image_from_db() results
# Key: (artist, album) tuple, Value: (timestamp, result_dict)
# Caches the result to avoid calling discover_custom_images on every poll cycle (10x per second)
_artist_image_load_cache: Dict[tuple, tuple] = {}

# Cache for ensure_artist_image_db results to prevent spamming checks
# Key: artist, Value: (timestamp, result_list)
_artist_db_check_cache: Dict[str, tuple] = {}

# ==========================================
# THROTTLES
# ==========================================

# Throttle for artist image fetch logs (prevents spam)
# Key: artist name, Value: last log timestamp
_artist_image_log_throttle: Dict[str, float] = {}

# ==========================================
# COUNTERS & TRACKING STATE
# ==========================================

# Track metadata fetch calls (not the same as API calls - one fetch may use cache)
_metadata_fetch_counters: Dict[str, int] = {'spotify': 0, 'windows_media': 0, 'spicetify': 0}

# Last state log time
_last_state_log_time: float = 0

# Track ID to avoid re-reading Windows thumbnail
_last_windows_track_id: Optional[str] = None

# Track last app_id to avoid log spam
_last_windows_app_id: Optional[str] = None

# Track when Windows media was last actively playing (for paused timeout)
_windows_last_active_time: float = 0

# Track when Spotify was last actively playing (for paused timeout)
_spotify_last_active_time: float = 0

# ==========================================
# SINGLETON PROVIDER INSTANCES
# ==========================================

# Global instance for ArtistImageProvider (singleton pattern)
# Will be lazily initialized when needed
_artist_image_provider: Optional[Any] = None
