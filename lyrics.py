import asyncio
import logging
import json
import os
import tempfile
from typing import Optional, List, Tuple, Dict, Set, Any

from system_utils import get_current_song_meta_data, create_tracked_task
from providers.lrclib import LRCLIBProvider
from providers.netease import NetEaseProvider
from providers.spotify_lyrics import SpotifyLyrics
from providers.qq import QQMusicProvider
from providers.musixmatch import MusixmatchProvider
from config import LYRICS, DEBUG, FEATURES, DATABASE_DIR
from logging_config import get_logger

logger = get_logger(__name__)

# Initialize providers
# Priority Order (from config.py):
# 1. Spotify (Priority 1) - Best for Spotify users
# 2. LRCLib (Priority 2) - Open Source, good quality
# 3. Musixmatch/NetEase (Priority 3) - Both at same priority, first to respond wins
# 4. QQ Music (Priority 4) - Fallback
providers = [
    LRCLIBProvider(),      # Priority 2
    SpotifyLyrics(),       # Priority 1
    MusixmatchProvider(),  # Priority 3
    NetEaseProvider(),     # Priority 3
    QQMusicProvider()      # Priority 4
]

# LATENCY_COMPENSATION = LYRICS.get("display", {}).get("latency_compensation", -0.1)
current_song_data = None
current_song_lyrics = None
current_song_word_synced_lyrics = None  # NEW: Current word-synced lyrics data
current_song_provider: Optional[str] = None  # Tracks which provider is currently serving lyrics
current_word_sync_provider: Optional[str] = None  # NEW: Tracks which provider is serving word-synced lyrics
_db_lock = asyncio.Lock()  # Protects read/modify/write cycle for DB files
_update_lock = asyncio.Lock()  # Protects against race conditions in `_update_song` - ensures only one song update happens at a time
_backfill_tracker: Set[str] = set()  # Avoid duplicate backfill runs per song

# ==========================================
# NEW: Local Database Helper Functions
# ==========================================

def _get_db_path(artist: str, title: str) -> Optional[str]:
    """Generates a safe filename for storing lyrics locally."""
    try:
        # Remove illegal characters for filenames to prevent errors
        safe_artist = "".join([c for c in artist if c.isalnum() or c in " -_"]).strip()
        safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
        filename = f"{safe_artist} - {safe_title}.json"
        return str(DATABASE_DIR / filename)
    except Exception:
        return None

def _load_from_db(artist: str, title: str) -> Optional[list]:
    """Loads lyrics from disk, prioritizing user preference or highest-quality provider available.
    
    Also loads word-synced lyrics if available, setting the global current_song_word_synced_lyrics.
    Word-sync priority: Musixmatch > NetEase (when auto-selecting).
    """
    global current_song_provider, current_song_word_synced_lyrics, current_word_sync_provider
    
    if not FEATURES.get("save_lyrics_locally", False): return None
    
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path): return None
    
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Reset word-sync state before loading
        current_song_word_synced_lyrics = None
        current_word_sync_provider = None
        
        # NEW FORMAT: Multi-provider storage
        if "saved_lyrics" in data and isinstance(data["saved_lyrics"], dict):
            saved_lyrics = data["saved_lyrics"]
            word_synced_lyrics = data.get("word_synced_lyrics", {})
            if not isinstance(word_synced_lyrics, dict):
                word_synced_lyrics = {}
            
            # Determine which provider to use for line-synced lyrics
            selected_provider = None
            selected_lyrics = None
            
            # Check for user's preferred provider first
            preferred_provider = data.get('preferred_provider')
            
            # NEW: word_sync_auto_switch setting - if enabled and preferred provider lacks word-sync,
            # but another provider HAS word-sync, override the preference to get word-sync
            word_sync_auto_switch = FEATURES.get("word_sync_auto_switch", False)
            should_use_preference = preferred_provider and preferred_provider in saved_lyrics
            
            if should_use_preference and word_sync_auto_switch:
                # Check if preferred provider has word-sync
                preferred_has_ws = (preferred_provider in word_synced_lyrics and 
                                   len(word_synced_lyrics.get(preferred_provider, [])) > 0)
                
                if not preferred_has_ws:
                    # Check if any other provider has word-sync
                    any_has_ws = any(
                        p.name in word_synced_lyrics and len(word_synced_lyrics.get(p.name, [])) > 0
                        for p in providers if p.name in saved_lyrics and p.name != preferred_provider
                    )
                    if any_has_ws:
                        logger.info(f"word_sync_auto_switch: Overriding preference '{preferred_provider}' (no word-sync) to find word-sync provider")
                        should_use_preference = False  # Fall through to auto-selection with word-sync boost
            
            if should_use_preference:
                selected_provider = preferred_provider
                selected_lyrics = saved_lyrics[preferred_provider]
                logger.info(f"Loaded lyrics from Local DB: {preferred_provider} (User Preference)")
            else:
                # If no preference (or overridden), find the BEST provider available
                # TWO-PASS SELECTION: Line-sync and word-sync are selected INDEPENDENTLY
                # This allows line-sync from Spotify (priority 1) while word-sync from NetEase/Musixmatch
                
                # PASS 1: Select LINE-SYNC provider using pure priority (NO boost)
                # Line-sync comes from the highest priority provider overall
                best_line_priority = 999
                for provider in providers:
                    if provider.name in saved_lyrics:
                        if provider.priority < best_line_priority:
                            best_line_priority = provider.priority
                            selected_lyrics = saved_lyrics[provider.name]
                            selected_provider = provider.name
                
                if selected_lyrics:
                    logger.info(f"Loaded line-sync from Local DB: {selected_provider} (priority {best_line_priority})")
            
            if selected_lyrics:
                current_song_provider = selected_provider
                
                # PASS 2: Select WORD-SYNC provider INDEPENDENTLY
                # First check for user's preferred word-sync provider
                preferred_ws_provider = data.get('preferred_word_sync_provider')
                
                if preferred_ws_provider and preferred_ws_provider in word_synced_lyrics:
                    ws_data = word_synced_lyrics.get(preferred_ws_provider, [])
                    if isinstance(ws_data, list) and len(ws_data) > 0:
                        current_song_word_synced_lyrics = ws_data
                        current_word_sync_provider = preferred_ws_provider
                        logger.info(f"Loaded word-sync from Local DB: {preferred_ws_provider} (User Preference)")
                
                # If no preference (or preference invalid), fall back to auto-selection
                if not current_word_sync_provider:
                    # Word-sync comes from the best provider that HAS word-sync data
                    # WORD_SYNC_BOOST ensures word-sync providers beat non-word-sync providers
                    # Among equal-priority word-sync providers, iteration order wins (Musixmatch before NetEase in list)
                    WORD_SYNC_BOOST = 10
                    best_ws_priority = 999
                    for provider in providers:
                        if provider.name in word_synced_lyrics:
                            ws_data = word_synced_lyrics.get(provider.name, [])
                            if isinstance(ws_data, list) and len(ws_data) > 0:
                                # Apply boost to ensure word-sync providers are prioritized
                                effective_priority = provider.priority - WORD_SYNC_BOOST
                                if effective_priority < best_ws_priority:
                                    best_ws_priority = effective_priority
                                    current_song_word_synced_lyrics = ws_data
                                    current_word_sync_provider = provider.name
                    
                    # Log word-sync selection result
                    if current_word_sync_provider:
                        logger.info(f"Loaded word-sync from Local DB: {current_word_sync_provider} (auto-selection)")
                    else:
                        # No provider has word-sync - that's fine, use line-sync only
                        current_song_word_synced_lyrics = None
                        current_word_sync_provider = None
                
                return selected_lyrics
        
        # LEGACY FORMAT: Single provider (backward compatibility)
        elif data.get('lyrics') and isinstance(data['lyrics'], list):
            source = data.get('source', 'Unknown')
            current_song_provider = source
            logger.info(f"Loaded lyrics from Local DB (legacy): {source}")
            return data['lyrics']
            
    except Exception as e:
        logger.error(f"Failed to load from Local DB: {e}")
    
    return None


def _has_any_word_sync_cached(artist: str, title: str) -> bool:
    """Check if any provider has word-synced lyrics cached for this song.
    
    Used by backfill logic to determine if we should try to fetch word-sync data.
    Returns True only if at least one provider has a non-empty list of word-synced lines.
    """
    if not FEATURES.get("save_lyrics_locally", False):
        return False

    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return False

    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        word_synced = data.get("word_synced_lyrics", {})
        
        # BUGFIX: Check for actual non-empty word-sync data, not just dict existence
        # This handles edge cases:
        # - {} (empty dict) -> False
        # - {"musixmatch": []} (empty list) -> False  
        # - {"musixmatch": None} (None value) -> False
        # - {"musixmatch": [{...}]} (valid data) -> True
        if not isinstance(word_synced, dict):
            return False
        
        return any(
            isinstance(v, list) and len(v) > 0 
            for v in word_synced.values()
        )
    except Exception:
        return False


def _get_saved_provider_names(artist: str, title: str) -> Set[str]:
    """Returns provider names already stored in the DB entry for this song."""
    if not FEATURES.get("save_lyrics_locally", False):
        return set()

    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return set()

    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "saved_lyrics" in data and isinstance(data["saved_lyrics"], dict):
            return set(data["saved_lyrics"].keys())
    except Exception as exc:
        logger.debug(f"Could not read provider list from DB ({artist} - {title}): {exc}")

    return set()


def _get_word_sync_provider_names(artist: str, title: str) -> Set[str]:
    """Returns provider names that have word-synced lyrics cached.
    
    Used by backfill logic to specifically check which providers have word-sync
    data (separate from line-sync data in saved_lyrics).
    """
    if not FEATURES.get("save_lyrics_locally", False):
        return set()

    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return set()

    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        word_synced = data.get("word_synced_lyrics", {})
        if isinstance(word_synced, dict):
            # BUGFIX: Only return providers with ACTUAL non-empty word-sync data
            # This prevents backfill from skipping providers that have empty lists
            return {k for k, v in word_synced.items() if isinstance(v, list) and len(v) > 0}
    except Exception:
        pass
    return set()


def get_song_word_sync_offset(artist: str, title: str) -> float:
    """
    Get per-song word-sync offset (seconds).
    Returns 0.0 if no offset saved for this song.
    """
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return 0.0
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return float(data.get("word_sync_offset", 0.0))
    except Exception:
        return 0.0


async def save_song_word_sync_offset(artist: str, title: str, offset: float) -> bool:
    """
    Save per-song word-sync offset (seconds).
    Creates or updates the song's JSON file with the offset.
    File I/O runs in thread pool to avoid blocking the event loop.
    Returns True on success.
    """
    db_path = _get_db_path(artist, title)
    if not db_path:
        return False
    
    # Clamp offset to reasonable range before file I/O
    clamped_offset = max(-5.0, min(5.0, offset))
    
    def _do_file_io():
        """Blocking file I/O - runs in thread pool."""
        # Load existing data or create minimal valid schema
        if os.path.exists(db_path):
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            # Create minimal valid schema to prevent partial DB files
            data = {
                "artist": artist,
                "title": title,
                "saved_lyrics": {},
                "word_synced_lyrics": {}
            }
        
        data["word_sync_offset"] = clamped_offset
        
        # Write atomically via temp file
        temp_path = db_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, db_path)
        return True
    
    async with _db_lock:
        try:
            await asyncio.to_thread(_do_file_io)
            logger.debug(f"Saved word-sync offset {offset:.3f}s for {artist} - {title}")
            return True
        except Exception as e:
            logger.error(f"Failed to save word-sync offset: {e}")
            return False


def _normalize_provider_result(result: Optional[Any]) -> Tuple[Optional[List[Tuple[float, str]]], Dict[str, Any], Optional[List[Dict[str, Any]]]]:
    """
    Normalize provider output into a lyrics list, metadata dict, and word-synced data.

    This allows new providers to return dictionaries while maintaining backwards
    compatibility with existing ones that return lists.
    
    Returns:
        Tuple of (lyrics, metadata, word_synced_lyrics)
        - lyrics: List of (timestamp, text) tuples for line-synced display
        - metadata: Dictionary with provider metadata (is_instrumental, etc.)
        - word_synced_lyrics: List of word-synced line dicts, or None if not available
    """
    if not result:
        return None, {}, None

    if isinstance(result, list):
        return result, {}, None

    if isinstance(result, dict):
        lyrics = result.get("lyrics")
        if not isinstance(lyrics, list):
            return None, {}, None

        # Extract word_synced_lyrics if present
        word_synced = result.get("word_synced_lyrics")
        
        # Build metadata from remaining keys (excluding lyrics and word_synced_lyrics)
        metadata = {key: value for key, value in result.items() 
                   if key not in ("lyrics", "word_synced_lyrics")}
        metadata.setdefault("is_instrumental", False)
        
        # Add flag indicating word-sync availability
        if word_synced:
            metadata["has_word_sync"] = True
        
        return lyrics, metadata, word_synced

    return None, {}, None


def _apply_instrumental_marker(lyrics: Optional[List[Tuple[float, str]]], metadata: Dict[str, Any]) -> Optional[List[Tuple[float, str]]]:
    """Ensures instrumental tracks at least have a single placeholder lyric."""
    if metadata.get("is_instrumental") and not lyrics:
        return [(0.0, "Instrumental")]
    return lyrics

def _is_manually_instrumental(artist: str, title: str) -> bool:
    """Checks if a song is manually marked as instrumental in the database."""
    if not FEATURES.get("save_lyrics_locally", False):
        return False
    
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return False
    
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Check for manual instrumental flag
        return data.get("is_instrumental_manual", False) is True
    except Exception as e:
        logger.debug(f"Could not check manual instrumental flag ({artist} - {title}): {e}")
        return False


def _is_cached_instrumental(artist: str, title: str) -> bool:
    """Returns True if cached metadata indicates the song is instrumental."""
    if not FEATURES.get("save_lyrics_locally", False):
        return False

    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return False

    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            return False

        for provider_meta in metadata.values():
            if isinstance(provider_meta, dict) and provider_meta.get("is_instrumental"):
                return True
    except Exception as exc:
        logger.debug(f"Could not read cached metadata for instrumental flag ({artist} - {title}): {exc}")

    return False

async def set_manual_instrumental(artist: str, title: str, is_instrumental: bool) -> bool:
    """
    Marks or unmarks a song as instrumental manually.
    File I/O runs in thread pool to avoid blocking the event loop.
    Returns True if successful, False otherwise.
    """
    if not FEATURES.get("save_lyrics_locally", False):
        return False
    
    db_path = _get_db_path(artist, title)
    if not db_path:
        return False
    
    def _do_file_io():
        """Blocking file I/O - runs in thread pool."""
        # Load existing file if it exists
        data = {
            "artist": artist,
            "title": title,
            "saved_lyrics": {}
        }
        
        if os.path.exists(db_path):
            try:
                with open(db_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                # Preserve existing structure
                if "saved_lyrics" in existing and isinstance(existing["saved_lyrics"], dict):
                    data = existing
                elif "lyrics" in existing:
                    # Legacy format - migrate
                    legacy_source = existing.get("source", "Unknown")
                    legacy_lyrics = existing.get("lyrics", [])
                    if legacy_lyrics:
                        data["saved_lyrics"][legacy_source] = legacy_lyrics
            except Exception as e:
                logger.warning(f"Could not load existing DB for instrumental marking: {e}")
        
        # Set or remove the manual flag
        if is_instrumental:
            data["is_instrumental_manual"] = True
        else:
            # Remove the flag if unmarking
            data.pop("is_instrumental_manual", None)
        
        # Save using atomic write pattern
        dir_path = os.path.dirname(db_path)
        fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_path, db_path)
        return True
    
    async with _db_lock:
        try:
            await asyncio.to_thread(_do_file_io)
            logger.info(f"Marked {artist} - {title} as {'instrumental' if is_instrumental else 'NOT instrumental'} (manual)")
            return True
        except Exception as e:
            logger.error(f"Failed to mark instrumental flag: {e}")
            return False

def _normalized_song_key(artist: str, title: str) -> str:
    """Creates a consistent key for tracking per-song background tasks."""
    return f"{artist.strip().lower()}::{title.strip().lower()}"

async def _save_to_db(artist: str, title: str, lyrics: list, source: str, 
                      metadata: Optional[Dict[str, Any]] = None,
                      word_synced: Optional[List[Dict[str, Any]]] = None) -> None:
    """Saves found lyrics to disk with multi-provider support (merge mode).
    
    File I/O runs in thread pool to avoid blocking the event loop.
    
    Args:
        artist: Artist name
        title: Song title
        lyrics: List of (timestamp, text) tuples for line-synced lyrics
        source: Provider name
        metadata: Optional metadata dict (is_instrumental, has_word_sync, etc.)
        word_synced: Optional list of word-synced line dicts
    """
    if not FEATURES.get("save_lyrics_locally", False) or not lyrics: return
    
    db_path = _get_db_path(artist, title)
    if not db_path: return

    def _do_file_io():
        """Blocking file I/O - runs in thread pool."""
        # Start with base structure
        data = {
            "artist": artist,
            "title": title,
            "saved_lyrics": {}  # Multi-provider storage
        }
        
        # Load existing file if it exists (for merging)
        if os.path.exists(db_path):
            try:
                with open(db_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                    
                # Check if it's the NEW format (has "saved_lyrics" dict)
                if "saved_lyrics" in existing and isinstance(existing["saved_lyrics"], dict):
                    data = existing  # Keep all existing providers and preferred_provider if present
                    
                # Migrate LEGACY format (single provider) to NEW format
                elif "lyrics" in existing and "source" in existing:
                    legacy_source = existing.get("source", "Unknown")
                    legacy_lyrics = existing.get("lyrics", [])
                    if legacy_lyrics:
                        data["saved_lyrics"][legacy_source] = legacy_lyrics
                        logger.info(f"Migrated legacy DB entry from {legacy_source}")
                        
            except Exception as e:
                logger.warning(f"Could not load existing DB, creating new: {e}")
        
        # Add/Update this provider's lyrics
        # Note: preferred_provider field is preserved from existing data (if present)
        # It should only be modified via set_provider_preference(), not during automatic saves
        data["saved_lyrics"][source] = lyrics
        if metadata:
            data.setdefault("metadata", {})
            data["metadata"][source] = metadata
        
        # NEW: Store word-synced lyrics if available
        if word_synced:
            data.setdefault("word_synced_lyrics", {})
            data["word_synced_lyrics"][source] = word_synced
            logger.debug(f"Stored {len(word_synced)} word-synced lines from {source}")
        
        # Save merged data using atomic write pattern
        # This prevents corruption if app crashes during write:
        # 1. Write to temp file first
        # 2. Use os.replace() to atomically swap (this is atomic on all platforms)
        dir_path = os.path.dirname(db_path)
        fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            # Atomic replace - if this fails, original file is untouched
            os.replace(temp_path, db_path)
        except Exception as write_err:
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            raise write_err
        
        return len(data['saved_lyrics'])

    async with _db_lock:
        try:
            # Run blocking file I/O in thread pool
            provider_count = await asyncio.to_thread(_do_file_io)
            
            # Log what we saved
            word_sync_status = f", word-sync from {source}" if word_synced else ""
            logger.info(f"Saved {source} lyrics to DB (now has {provider_count} providers{word_sync_status})")
        except Exception as e:
            logger.error(f"Failed to save to DB: {e}")


def _save_all_results_background(
    artist: str,
    title: str,
    pending_tasks: Set[asyncio.Task],
    provider_map: Dict[asyncio.Task, object],
    timeout: float = 10.0
) -> None:
    """Continues collecting provider results after we already returned lyrics."""

    async def collect_remaining() -> None:
        """Waits for remaining providers, saves finished ones, cancels laggards."""
        try:
            if pending_tasks:
                done, still_pending = await asyncio.wait(pending_tasks, timeout=timeout)

                for task in done:
                    provider = provider_map.get(task)
                    if not provider:
                        continue

                    try:
                        raw_result = await task
                        lyrics, metadata, word_synced = _normalize_provider_result(raw_result)
                        lyrics = _apply_instrumental_marker(lyrics, metadata)
                        if lyrics:
                            await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata, word_synced=word_synced)
                            logger.info(f"Background save complete for {provider.name}")
                            
                            # FIX: Reload word-sync if saved and same song still playing
                            # Without this, word-sync from slow providers (e.g., Musixmatch) would be
                            # saved to DB but never loaded into current_song_word_synced_lyrics,
                            # causing the word-sync toggle to appear unavailable for that session.
                            # This mirrors the existing reload logic in _backfill_missing_providers.
                            if word_synced and current_song_data:
                                if (current_song_data.get("artist") == artist and 
                                    current_song_data.get("title") == title):
                                    reloaded = _load_from_db(artist, title)
                                    if reloaded:
                                        global current_song_lyrics
                                        current_song_lyrics = reloaded
                                    logger.info(f"Reloaded word-sync after background save from {provider.name}")
                    except Exception as exc:
                        logger.debug(f"Background provider error ({provider.name}): {exc}")

                for task in still_pending:
                    task.cancel()
        except Exception as exc:
            logger.error(f"Background collection error: {exc}")

    create_tracked_task(collect_remaining())

def _backfill_missing_providers(
    artist: str,
    title: str,
    missing_providers: List[object],
    skip_provider_limit: bool = False,
    album: str = None,
    duration: int = None,
    force: bool = False
) -> None:
    """Fetches any providers that are missing in the DB while UI uses cached lyrics.
    
    Args:
        artist: Artist name
        title: Song title
        missing_providers: List of provider objects to fetch from
        skip_provider_limit: If True, skip the 3-provider limit check (used for word-sync backfill)
        album: Album name for provider matching (optional)
        duration: Track duration in seconds (optional)
        force: If True, bypass the duplicate-run tracker (for manual refetch requests)
    """
    song_key = _normalized_song_key(artist, title)
    
    # Skip tracker check if force=True (manual refetch request)
    if not force:
        if song_key in _backfill_tracker:
            return

    _backfill_tracker.add(song_key)

    async def run_backfill() -> None:
        """
        Runs each missing provider without blocking the main playback.
        Stops once we have 3 providers saved to avoid unnecessary requests.
        """
        try:
            tasks: Set[asyncio.Task] = set()
            provider_map: Dict[asyncio.Task, object] = {}

            for provider in missing_providers:
                if asyncio.iscoroutinefunction(provider.get_lyrics):
                    coro = provider.get_lyrics(artist, title, album, duration)
                else:
                    coro = asyncio.to_thread(provider.get_lyrics, artist, title, album, duration)

                task = asyncio.create_task(coro)
                tasks.add(task)
                provider_map[task] = provider

            if not tasks:
                return

            # Use asyncio.wait() instead of as_completed() to preserve original task objects
            # This ensures provider_map.get(task) works correctly (as_completed returns wrapper futures)
            pending = tasks

            while pending:
                # Check if we already have 3 providers saved - if so, stop backfilling
                # Skip this check for word-sync backfill (skip_provider_limit=True)
                if not skip_provider_limit:
                    saved_providers = _get_saved_provider_names(artist, title)
                    if len(saved_providers) >= 3:
                        logger.info(f"Backfill stopped for {artist} - {title} (reached 3 providers: {', '.join(saved_providers)})")
                        # Cancel remaining tasks to avoid unnecessary requests
                        for task in pending:
                            task.cancel()
                        break

                # Wait for at least one task to complete
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

                # Process all completed tasks
                for task in done:
                    provider = provider_map.get(task)
                    if not provider:
                        continue

                    try:
                        raw_result = await task
                        lyrics, metadata, word_synced = _normalize_provider_result(raw_result)
                        lyrics = _apply_instrumental_marker(lyrics, metadata)
                        if lyrics:
                            await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata, word_synced=word_synced)
                            logger.info(f"Backfill saved lyrics from {provider.name}")
                            
                            # FIX: If word-sync was saved and this is the same song still playing,
                            # reload from DB to update the global word-sync variables.
                            # This ensures word-sync appears immediately after backfill completes
                            # without requiring the user to re-select the provider.
                            if word_synced and current_song_data:
                                if (current_song_data.get("artist") == artist and 
                                    current_song_data.get("title") == title):
                                    # Reload from DB to pick up the new word-sync data
                                    # CRITICAL: Use return value to update current_song_lyrics
                                    # so lyrics match the newly selected word-sync provider
                                    reloaded = _load_from_db(artist, title)
                                    if reloaded:
                                        global current_song_lyrics
                                        current_song_lyrics = reloaded
                                    logger.info(f"Reloaded word-sync after backfill from {provider.name}")
                            
                            # Check again after saving - if we now have 3 providers, stop
                            # Skip this check for word-sync backfill (skip_provider_limit=True)
                            if not skip_provider_limit:
                                saved_providers = _get_saved_provider_names(artist, title)
                                if len(saved_providers) >= 3:
                                    logger.info(f"Backfill completed for {artist} - {title} (reached 3 providers: {', '.join(saved_providers)})")
                                    # Cancel remaining tasks
                                    for task in pending:
                                        task.cancel()
                                    pending = set()  # Clear pending to exit loop
                                    break
                    except Exception as exc:
                        logger.debug(f"Backfill provider error ({getattr(provider, 'name', 'Unknown')}): {exc}")
        finally:
            _backfill_tracker.discard(song_key)

    create_tracked_task(run_backfill())

# ==========================================
# Provider Management Functions
# ==========================================

def get_current_provider() -> Optional[str]:
    """Returns the name of the provider currently serving lyrics."""
    return current_song_provider

def get_available_providers_for_song(artist: str, title: str) -> List[Dict[str, Any]]:
    """
    Returns list of providers that have lyrics for this song.
    
    Returns:
        List of dicts with: {
            'name': str,
            'priority': int,
            'cached': bool,
            'is_current': bool,
            'has_word_sync': bool  # Whether this provider has word-synced lyrics cached
        }
    """
    # Check database for cached providers
    saved_providers = _get_saved_provider_names(artist, title)
    
    # Check which providers have word-synced lyrics cached and get word-sync preference
    word_sync_providers = set()
    preferred_ws_provider = None
    db_path = _get_db_path(artist, title)
    if db_path and os.path.exists(db_path):
        try:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            word_synced = data.get("word_synced_lyrics", {})
            word_sync_providers = set(word_synced.keys())
            preferred_ws_provider = data.get("preferred_word_sync_provider")
        except Exception:
            pass
    
    result = []
    for provider in providers:
        if not provider.enabled:
            continue
            
        result.append({
            'name': provider.name,
            'priority': provider.priority,
            'cached': provider.name in saved_providers,
            'is_current': provider.name == current_song_provider,
            'is_word_sync_current': provider.name == current_word_sync_provider,
            'is_word_sync_preferred': provider.name == preferred_ws_provider,
            'has_word_sync': provider.name in word_sync_providers
        })
    
    # Sort by priority for consistent ordering
    return sorted(result, key=lambda x: x['priority'])

async def set_provider_preference(artist: str, title: str, provider_name: str) -> Dict[str, Any]:
    """
    Set user's preferred provider for a specific song.
    
    Returns:
        {
            'status': 'success' | 'error',
            'message': str,
            'lyrics': Optional[list],  # New lyrics if fetched
            'provider': str  # Name of provider now being used
        }
    """
    global current_song_provider, current_song_lyrics, current_song_word_synced_lyrics, current_word_sync_provider
    
    # Validate provider exists and is enabled
    provider_obj = None
    for p in providers:
        if p.name == provider_name and p.enabled:
            provider_obj = p
            break
    
    if not provider_obj:
        return {'status': 'error', 'message': f'Provider {provider_name} not available'}
    
    # Check if lyrics are already in DB
    db_path = _get_db_path(artist, title)
    if db_path and os.path.exists(db_path):
        async with _db_lock:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Check if this provider's lyrics exist
            if 'saved_lyrics' in data and provider_name in data['saved_lyrics']:
                # Use cached lyrics
                lyrics = data['saved_lyrics'][provider_name]
                current_song_lyrics = lyrics
                current_song_provider = provider_name
                
                # FIX: Word-sync is INDEPENDENT of line-sync preference
                # When selecting a line-sync provider, keep best word-sync provider or user's word-sync preference
                word_synced_cache = data.get('word_synced_lyrics', {})
                ws_loaded = False
                
                # First check: does the selected provider have word-sync?
                if provider_name in word_synced_cache:
                    ws_data = word_synced_cache.get(provider_name, [])
                    if isinstance(ws_data, list) and len(ws_data) > 0:
                        current_song_word_synced_lyrics = ws_data
                        current_word_sync_provider = provider_name
                        ws_loaded = True
                        logger.debug(f"Loaded {len(ws_data)} word-synced lines from {provider_name} (same as line-sync)")
                
                # If not, check user's word-sync preference
                if not ws_loaded:
                    preferred_ws_provider = data.get('preferred_word_sync_provider')
                    if preferred_ws_provider and preferred_ws_provider in word_synced_cache:
                        ws_data = word_synced_cache.get(preferred_ws_provider, [])
                        if isinstance(ws_data, list) and len(ws_data) > 0:
                            current_song_word_synced_lyrics = ws_data
                            current_word_sync_provider = preferred_ws_provider
                            ws_loaded = True
                            logger.debug(f"Loaded word-sync from {preferred_ws_provider} (User Preference) while using {provider_name} for line-sync")
                
                # If still not, auto-select best word-sync provider
                if not ws_loaded:
                    WORD_SYNC_BOOST = 10
                    best_ws_priority = 999
                    for p in providers:
                        if p.name in word_synced_cache:
                            ws_data = word_synced_cache.get(p.name, [])
                            if isinstance(ws_data, list) and len(ws_data) > 0:
                                effective_priority = p.priority - WORD_SYNC_BOOST
                                if effective_priority < best_ws_priority:
                                    best_ws_priority = effective_priority
                                    current_song_word_synced_lyrics = ws_data
                                    current_word_sync_provider = p.name
                                    ws_loaded = True
                    
                    if ws_loaded:
                        logger.debug(f"Loaded word-sync from {current_word_sync_provider} (auto-selection) while using {provider_name} for line-sync")
                    else:
                        # No word-sync available at all
                        current_song_word_synced_lyrics = None
                        current_word_sync_provider = None
                
                # Update preference in DB using atomic write pattern
                # FIX: Use temp file to prevent race conditions during rapid song skipping
                data['preferred_provider'] = provider_name
                dir_path = os.path.dirname(db_path)
                try:
                    # Create temp file in same directory (required for atomic rename)
                    fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
                    # Atomic replace - if this fails, original file is untouched
                    os.replace(temp_path, db_path)
                except Exception as write_err:
                    # Clean up temp file if it exists
                    if 'temp_path' in locals() and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                    raise write_err
                
                logger.info(f"Switched to cached {provider_name} lyrics")
                return {
                    'status': 'success',
                    'message': f'Switched to {provider_name}',
                    'lyrics': lyrics,
                    'provider': provider_name
                }
    
    # Lyrics not cached - fetch them
    try:
        # Get album/duration from current song data for better provider matching
        album = None
        duration = None
        if current_song_data:
            album = current_song_data.get("album")
            duration_ms = current_song_data.get("duration_ms")
            duration = duration_ms // 1000 if duration_ms else None
        
        if asyncio.iscoroutinefunction(provider_obj.get_lyrics):
            raw_result = await provider_obj.get_lyrics(artist, title, album, duration)
        else:
            raw_result = await asyncio.to_thread(provider_obj.get_lyrics, artist, title, album, duration)

        lyrics, metadata, word_synced = _normalize_provider_result(raw_result)
        lyrics = _apply_instrumental_marker(lyrics, metadata)

        if lyrics:
            # Save to DB with preference
            await _save_to_db(artist, title, lyrics, provider_name, metadata=metadata, word_synced=word_synced)
            
            # Update preference in DB using atomic write pattern
            # FIX: Use temp file to prevent race conditions during rapid song skipping
            db_path = _get_db_path(artist, title)
            if db_path:
                async with _db_lock:
                    with open(db_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    data['preferred_provider'] = provider_name
                    dir_path = os.path.dirname(db_path)
                    try:
                        # Create temp file in same directory (required for atomic rename)
                        fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
                        with os.fdopen(fd, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=4, ensure_ascii=False)
                        # Atomic replace - if this fails, original file is untouched
                        os.replace(temp_path, db_path)
                    except Exception as write_err:
                        # Clean up temp file if it exists
                        if 'temp_path' in locals() and os.path.exists(temp_path):
                            try:
                                os.unlink(temp_path)
                            except:
                                pass
                        raise write_err
            
            # Update current state
            current_song_lyrics = lyrics
            current_song_provider = provider_name
            
            # FIX: Word-sync is INDEPENDENT of line-sync preference
            # If the fetched provider has word-sync, use it
            # Otherwise, check DB for existing word-sync from other providers
            if word_synced:
                current_song_word_synced_lyrics = word_synced
                current_word_sync_provider = provider_name
                logger.debug(f"Loaded {len(word_synced)} word-synced lines from freshly fetched {provider_name}")
            else:
                # Provider doesn't have word-sync - check DB for existing word-sync
                db_path = _get_db_path(artist, title)
                ws_loaded = False
                if db_path and os.path.exists(db_path):
                    try:
                        with open(db_path, 'r', encoding='utf-8') as f:
                            db_data = json.load(f)
                        word_synced_cache = db_data.get('word_synced_lyrics', {})
                        
                        # Check user's word-sync preference first
                        preferred_ws_provider = db_data.get('preferred_word_sync_provider')
                        if preferred_ws_provider and preferred_ws_provider in word_synced_cache:
                            ws_data = word_synced_cache.get(preferred_ws_provider, [])
                            if isinstance(ws_data, list) and len(ws_data) > 0:
                                current_song_word_synced_lyrics = ws_data
                                current_word_sync_provider = preferred_ws_provider
                                ws_loaded = True
                                logger.debug(f"Kept word-sync from {preferred_ws_provider} (User Preference) while fetching {provider_name} for line-sync")
                        
                        # If no preference, auto-select best
                        if not ws_loaded:
                            WORD_SYNC_BOOST = 10
                            best_ws_priority = 999
                            for p in providers:
                                if p.name in word_synced_cache:
                                    ws_data = word_synced_cache.get(p.name, [])
                                    if isinstance(ws_data, list) and len(ws_data) > 0:
                                        effective_priority = p.priority - WORD_SYNC_BOOST
                                        if effective_priority < best_ws_priority:
                                            best_ws_priority = effective_priority
                                            current_song_word_synced_lyrics = ws_data
                                            current_word_sync_provider = p.name
                                            ws_loaded = True
                            
                            if ws_loaded:
                                logger.debug(f"Kept word-sync from {current_word_sync_provider} (auto-selection) while fetching {provider_name} for line-sync")
                    except Exception as e:
                        logger.debug(f"Could not load existing word-sync from DB: {e}")
                
                if not ws_loaded:
                    # No word-sync available at all
                    current_song_word_synced_lyrics = None
                    current_word_sync_provider = None
            
            logger.info(f"Fetched and switched to {provider_name} lyrics")
            return {
                'status': 'success',
                'message': f'Switched to {provider_name}',
                'lyrics': lyrics,
                'provider': provider_name
            }
        else:
            return {
                'status': 'error',
                'message': f'{provider_name} has no lyrics for this song'
            }
    except Exception as e:
        logger.error(f"Error fetching from {provider_name}: {e}")
        return {
            'status': 'error',
            'message': f'Failed to fetch from {provider_name}: {str(e)}'
        }

async def clear_provider_preference(artist: str, title: str) -> bool:
    """
    Clear manual provider preference, return to automatic selection.
    
    Returns:
        True if preference was cleared, False if error
    """
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return True  # No preference to clear
    
    try:
        async with _db_lock:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if 'preferred_provider' in data:
                del data['preferred_provider']
                
                # FIX: Use temp file to prevent race conditions during rapid song skipping
                dir_path = os.path.dirname(db_path)
                try:
                    # Create temp file in same directory (required for atomic rename)
                    fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
                    # Atomic replace - if this fails, original file is untouched
                    os.replace(temp_path, db_path)
                except Exception as write_err:
                    # Clean up temp file if it exists
                    if 'temp_path' in locals() and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                    raise write_err
                
                logger.info(f"Cleared provider preference for {artist} - {title}")
        
        # Reload lyrics with automatic selection
        await _update_song()
        return True
    except Exception as e:
        logger.error(f"Error clearing preference: {e}")
        return False

async def set_word_sync_provider_preference(artist: str, title: str, provider_name: str) -> Dict[str, Any]:
    """
    Set user's preferred word-sync provider for a specific song.
    
    Args:
        artist: Artist name
        title: Song title
        provider_name: Name of provider to use for word-sync
        
    Returns:
        {
            'status': 'success' | 'error',
            'message': str,
            'word_sync_provider': str  # Name of provider now being used for word-sync
        }
    """
    global current_song_word_synced_lyrics, current_word_sync_provider
    
    # Validate provider exists
    provider_obj = None
    for p in providers:
        if p.name == provider_name and p.enabled:
            provider_obj = p
            break
    
    if not provider_obj:
        return {'status': 'error', 'message': f'Provider {provider_name} not available'}
    
    # Check if provider has word-sync cached
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return {'status': 'error', 'message': 'No lyrics cached for this song'}
    
    try:
        async with _db_lock:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            word_synced_lyrics = data.get("word_synced_lyrics", {})
            if provider_name not in word_synced_lyrics:
                return {'status': 'error', 'message': f'{provider_name} has no word-sync for this song'}
            
            ws_data = word_synced_lyrics.get(provider_name, [])
            if not isinstance(ws_data, list) or len(ws_data) == 0:
                return {'status': 'error', 'message': f'{provider_name} has no word-sync for this song'}
            
            # Save preference
            data['preferred_word_sync_provider'] = provider_name
            
            # Write atomically
            dir_path = os.path.dirname(db_path)
            try:
                fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                os.replace(temp_path, db_path)
            except Exception as write_err:
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                raise write_err
            
            # Update current state ONLY if this song is still playing
            # (prevents race condition if user skipped songs during API call)
            if (current_song_data and 
                current_song_data.get('artist') == artist and 
                current_song_data.get('title') == title):
                current_song_word_synced_lyrics = ws_data
                current_word_sync_provider = provider_name
            
            logger.info(f"Set word-sync provider preference to {provider_name} for {artist} - {title}")
            return {
                'status': 'success',
                'message': f'Word-sync now from {provider_name}',
                'word_sync_provider': provider_name
            }
    except Exception as e:
        logger.error(f"Error setting word-sync preference: {e}")
        return {'status': 'error', 'message': f'Failed to set preference: {str(e)}'}

async def clear_word_sync_provider_preference(artist: str, title: str) -> bool:
    """
    Clear word-sync provider preference, return to automatic selection.
    
    Returns:
        True if preference was cleared, False if error
    """
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return True  # No preference to clear
    
    try:
        async with _db_lock:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if 'preferred_word_sync_provider' in data:
                del data['preferred_word_sync_provider']
                
                # Write atomically
                dir_path = os.path.dirname(db_path)
                try:
                    fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
                    os.replace(temp_path, db_path)
                except Exception as write_err:
                    if 'temp_path' in locals() and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                    raise write_err
                
                logger.info(f"Cleared word-sync provider preference for {artist} - {title}")
        
        # Reload to re-select word-sync automatically
        reloaded = _load_from_db(artist, title)
        if reloaded:
            global current_song_lyrics
            current_song_lyrics = reloaded
        return True
    except Exception as e:
        logger.error(f"Error clearing word-sync preference: {e}")
        return False

async def delete_cached_lyrics(artist: str, title: str) -> Dict[str, Any]:
    """
    Delete all cached lyrics for a song from the local database.
    Use this when cached lyrics are wrong and you want to re-fetch from providers.
    
    Returns:
        {
            'status': 'success' | 'error',
            'message': str
        }
    """
    global current_song_lyrics, current_song_provider
    
    db_path = _get_db_path(artist, title)
    
    if not db_path:
        return {'status': 'error', 'message': 'Could not determine database path'}
    
    if not os.path.exists(db_path):
        return {'status': 'success', 'message': 'No cached lyrics to delete'}
    
    try:
        async with _db_lock:
            os.remove(db_path)
            logger.info(f"Deleted cached lyrics for {artist} - {title}")
        
        # Clear current lyrics so they get re-fetched
        current_song_lyrics = None
        current_song_provider = None
        
        # Trigger re-fetch by forcing an update
        # We reset the song data to force a fresh fetch on next poll
        global current_song_data
        current_song_data = None
        
        return {'status': 'success', 'message': 'Cached lyrics deleted. Will re-fetch on next update.'}
    except Exception as e:
        logger.error(f"Error deleting cached lyrics: {e}")
        return {'status': 'error', 'message': f'Failed to delete: {str(e)}'}


async def refetch_lyrics(artist: str, title: str, album: str = None, duration: int = None) -> Dict[str, Any]:
    """
    Manually trigger lyrics refetch from ALL enabled providers.
    Unlike automatic backfill, this forces fetching even if lyrics already exist.
    
    Args:
        artist: Artist name
        title: Song title
        album: Album name (optional)
        duration: Track duration in seconds (optional)
    
    Returns:
        {
            'status': 'success' | 'error',
            'message': str,
            'providers_count': int  # Number of providers being fetched
        }
    """
    if not artist or not title:
        return {'status': 'error', 'message': 'Artist and title are required'}
    
    # Get all enabled providers
    enabled_providers = [p for p in providers if p.enabled]
    
    if not enabled_providers:
        return {'status': 'error', 'message': 'No providers enabled'}
    
    # Trigger backfill with force=True - skips tracker check and fetches from all
    _backfill_missing_providers(
        artist, 
        title, 
        enabled_providers, 
        skip_provider_limit=True,  # Don't stop at 3 providers
        album=album, 
        duration=duration,
        force=True  # Bypass the duplicate-run tracker
    )
    
    logger.info(f"Manual lyrics refetch triggered for {artist} - {title} ({len(enabled_providers)} providers)")
    
    return {
        'status': 'success', 
        'message': f'Refetching lyrics from {len(enabled_providers)} providers...',
        'providers_count': len(enabled_providers)
    }


# ==========================================
# Main Logic
# ==========================================

async def _fetch_and_set_lyrics(target_artist: str, target_title: str, 
                                 album: str = None, duration: int = None):
    """
    Background task helper to fetch lyrics without blocking the UI.
    
    This function runs in the background after _update_song has already
    updated current_song_data and released the lock. This prevents the
    UI from freezing while waiting for internet requests to complete.
    
    Args:
        target_artist: Artist name
        target_title: Song title
        album: Album name for better provider matching (optional)
        duration: Track duration in seconds (optional)
    
    After fetching, reloads from DB to populate word-sync globals.
    """
    global current_song_lyrics, current_song_data, current_song_provider
    global current_song_word_synced_lyrics, current_word_sync_provider

    try:
        # Use the global _get_lyrics function to fetch from internet providers
        # Pass album/duration for better provider matching (scoring)
        fetched_lyrics = await _get_lyrics(target_artist, target_title, album, duration)
        
        # CRITICAL: Check if song is still the same before setting lyrics
        # This prevents stale lyrics from a previous song being displayed
        # if the user skipped to a new song while this fetch was in progress
        if (current_song_data and 
            current_song_data["artist"] == target_artist and 
            current_song_data["title"] == target_title):
            current_song_lyrics = fetched_lyrics
            
            # BUGFIX: Reload from DB to populate word-sync globals AND get correctly selected lyrics
            # _get_lyrics() saves word-sync to DB but doesn't return it.
            # Loading from DB also applies word-sync boost to select the best provider.
            # CRITICAL: We must use the return value to update current_song_lyrics,
            # otherwise there's a mismatch between current_song_provider and current_song_lyrics.
            reloaded_lyrics = _load_from_db(target_artist, target_title)
            if reloaded_lyrics:
                current_song_lyrics = reloaded_lyrics
            
            logger.info(f"Background fetch completed for {target_artist} - {target_title}")
        else:
            # Song changed during fetch - discard these lyrics to prevent wrong display
            logger.debug(f"Discarded background lyrics for {target_artist} - {target_title} (song changed)")
    except Exception as e:
        logger.error(f"Error in background fetch for {target_artist}: {e}")

async def _update_song():
    """
    Updates current song data and fetches lyrics if changed.
    
    CRITICAL: Updates current_song_data IMMEDIATELY when song changes to prevent
    race conditions where lyrics from the previous song are displayed after
    a rapid song change.
    
    Uses a lock to ensure only one update happens at a time, preventing
    concurrent calls from causing inconsistent state.
    """
    global current_song_lyrics, current_song_data, current_song_provider
    global current_song_word_synced_lyrics, current_word_sync_provider

    # CRITICAL FIX: Use lock to prevent concurrent updates
    # This ensures only one song update happens at a time, preventing race conditions
    # where multiple calls to _update_song() could interleave and cause wrong lyrics
    # to be displayed for the current song
    async with _update_lock:
        new_song_data = await get_current_song_meta_data()

        # If no song or empty song or no artist or no title, clear lyrics
        if new_song_data is None or (not new_song_data["artist"].strip() or not new_song_data["title"].strip()):
            # Throttled log: only log once every 60 seconds to prevent spam
            if new_song_data is not None:
                import time
                from system_utils import state
                current_time = time.time()
                if current_time - state._lyrics_skip_last_log_time >= state._LYRICS_SKIP_LOG_INTERVAL:
                    state._lyrics_skip_last_log_time = current_time
                    artist = new_song_data.get("artist", "") or "(empty)"
                    title = new_song_data.get("title", "") or "(empty)"
                    logger.debug(f"Skipping lyrics: incomplete metadata - artist: '{artist}', title: '{title}'")
            
            current_song_lyrics = None
            current_song_data = new_song_data
            # BUGFIX: Also clear word-sync state when no song is playing
            current_song_word_synced_lyrics = None
            current_word_sync_provider = None
            return

        # Check if song changed
        should_fetch_lyrics = current_song_data is None or (
            current_song_data["artist"] != new_song_data["artist"] or
            current_song_data["title"] != new_song_data["title"]
        )

        if should_fetch_lyrics:
            # CRITICAL FIX: Clear old lyrics and update current_song_data IMMEDIATELY when song changes
            # This prevents race conditions where:
            # 1. Song B starts while fetching lyrics for Song A
            # 2. The system doesn't know the song changed because current_song_data wasn't updated
            # 3. Lyrics from Song A get displayed for Song B
            current_song_lyrics = None  # Clear old lyrics immediately to prevent stale display
            current_song_data = new_song_data
            
            # Reset provider when song changes so UI shows correct info during fetch
            # This prevents showing the previous song's provider while searching for new lyrics
            current_song_provider = None
            
            # BUGFIX: Also reset word-sync globals to prevent stale word-sync from previous song
            current_song_word_synced_lyrics = None
            current_word_sync_provider = None
            
            # Store song identifier to validate after async fetch completes
            # This ensures we don't set lyrics if the song changed again during fetch
            target_artist = new_song_data["artist"]
            target_title = new_song_data["title"]
            
            # Check if song is manually marked as instrumental
            # If so, skip all lyrics searching and mark as instrumental immediately
            if _is_manually_instrumental(target_artist, target_title):
                logger.info(f"Song {target_artist} - {target_title} is manually marked as instrumental, skipping lyrics search")
                # Set instrumental marker as lyrics (single line with instrumental indicator)
                current_song_lyrics = [(0, "Instrumental")]
                current_song_provider = "Instrumental"
                return  # Skip all provider searches

            if _is_cached_instrumental(target_artist, target_title):
                logger.info(f"Song {target_artist} - {target_title} is cached as instrumental, skipping lyrics search")
                current_song_lyrics = [(0, "Instrumental")]
                current_song_provider = "Instrumental (cached)"
                return
            
            # 1. Try Local DB First (Zero Latency)
            local_lyrics = _load_from_db(target_artist, target_title)
            if local_lyrics:
                # Validate song hasn't changed during DB load (should be instant, but be safe)
                if (current_song_data and 
                    current_song_data["artist"] == target_artist and 
                    current_song_data["title"] == target_title):
                    current_song_lyrics = local_lyrics

                    saved_providers = _get_saved_provider_names(target_artist, target_title)
                    has_word_sync = _has_any_word_sync_cached(target_artist, target_title)
                    
                    # Backfill if:
                    # 1. We have fewer than 3 providers saved, OR
                    # 2. No word-sync lyrics are cached yet (to get karaoke data over time)
                    # This ensures existing songs eventually get word-synced data without
                    # being too aggressive about refetching.
                    should_backfill = len(saved_providers) < 3 or not has_word_sync
                    
                    if should_backfill:
                        # For word-sync backfill, prioritize Musixmatch/NetEase
                        if not has_word_sync:
                            # Specifically target word-sync providers
                            # FIX: Check word_sync_saved instead of saved_providers
                            # This ensures we fetch word-sync even if line-sync already cached
                            word_sync_providers = ["musixmatch", "netease"]
                            word_sync_saved = _get_word_sync_provider_names(target_artist, target_title)
                            missing = [
                                provider
                                for provider in providers
                                if provider.enabled and provider.name in word_sync_providers and provider.name not in word_sync_saved
                            ]
                            if missing:
                                logger.info(f"Word-sync backfill triggered for {target_artist} - {target_title} (no word-sync cached, trying: {', '.join(p.name for p in missing)})")
                                backfill_album = new_song_data.get("album")
                                backfill_duration_ms = new_song_data.get("duration_ms")
                                backfill_duration = backfill_duration_ms // 1000 if backfill_duration_ms else None
                                _backfill_missing_providers(target_artist, target_title, missing, skip_provider_limit=True, album=backfill_album, duration=backfill_duration)
                        else:
                            # Normal backfill for providers < 3
                            missing = [
                                provider
                                for provider in providers
                                if provider.enabled and provider.name not in saved_providers
                            ]
                            if missing:
                                logger.info(f"Backfill triggered for {target_artist} - {target_title} (have {len(saved_providers)}/3 providers, missing: {', '.join(p.name for p in missing)})")
                                backfill_album = new_song_data.get("album")
                                backfill_duration_ms = new_song_data.get("duration_ms")
                                backfill_duration = backfill_duration_ms // 1000 if backfill_duration_ms else None
                                _backfill_missing_providers(target_artist, target_title, missing, album=backfill_album, duration=backfill_duration)
                    else:
                        logger.debug(f"Skipping backfill for {target_artist} - {target_title} (have {len(saved_providers)} providers, word-sync: {has_word_sync})")
            else:
                # 2. Try Internet (Smart Race) - BACKGROUND
                # CRITICAL PERFORMANCE FIX: Don't await internet fetch inside lock
                # Starting a background task allows the UI to remain responsive while
                # lyrics are being fetched from providers. The lock is released immediately
                # so other operations can continue, and _fetch_and_set_lyrics will update
                # current_song_lyrics when the fetch completes (if song hasn't changed).
                current_song_lyrics = [(0, "Searching lyrics...")] 
                # Pass album and duration for better provider matching
                target_album = new_song_data.get("album")
                target_duration_ms = new_song_data.get("duration_ms")
                target_duration = target_duration_ms // 1000 if target_duration_ms else None
                create_tracked_task(_fetch_and_set_lyrics(target_artist, target_title, target_album, target_duration))
        else:
            # Song hasn't changed, just update the metadata (position, etc.)
            current_song_data = new_song_data

async def _get_lyrics(artist: str, title: str, album: str = None, duration: int = None):
    """
    Tries providers to find lyrics.
    
    Args:
        artist: Artist name
        title: Song title
        album: Album name for better matching (optional)
        duration: Track duration in seconds for scoring (optional)
    
    Modes:
    1. Sequential: Tries one by one. Safe, but slow.
    2. Parallel (Smart): Tries all at once. 
       - Prioritizes High Quality (LRCLib/Spotify).
       - If Low Quality (QQ/NetEase) comes first, waits a configurable grace period for High Quality before giving up.
    """
    global current_song_provider
    
    active_providers = [p for p in providers if p.enabled]
    sorted_providers = sorted(active_providers, key=lambda x: x.priority)

    # --- SEQUENTIAL MODE (Safe Mode) ---
    # This mode is used if Parallel Fetching is disabled in config
    if not FEATURES.get("parallel_provider_fetch", True):
        best_lyrics = None
        best_provider_name = None
        for provider in sorted_providers:
            try:
                if asyncio.iscoroutinefunction(provider.get_lyrics):
                    raw_result = await provider.get_lyrics(artist, title, album, duration)
                else:
                    raw_result = await asyncio.to_thread(provider.get_lyrics, artist, title, album, duration)

                lyrics, metadata, word_synced = _normalize_provider_result(raw_result)
                lyrics = _apply_instrumental_marker(lyrics, metadata)

                if lyrics:
                    logger.info(f"Found lyrics using {provider.name}")
                    await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata, word_synced=word_synced)
                    if best_lyrics is None:
                        best_lyrics = lyrics
                        best_provider_name = provider.name
            except Exception as e:
                logger.error(f"Error with {provider.name}: {e}")
        if best_provider_name:
            current_song_provider = best_provider_name
        return best_lyrics

    # --- PARALLEL MODE (Fast Mode with Smart Priority) ---
    tasks = []
    provider_map = {} # Map tasks to provider objects

    for provider in sorted_providers:
        # Wrap sync functions in thread, keep async functions as is
        if asyncio.iscoroutinefunction(provider.get_lyrics):
            coro = provider.get_lyrics(artist, title, album, duration)
        else:
            coro = asyncio.to_thread(provider.get_lyrics, artist, title, album, duration)
        
        task = asyncio.create_task(coro)
        tasks.append(task)
        provider_map[task] = provider

    if not tasks: return None
    
    pending = set(tasks)
    best_result = None
    best_priority = 999
    best_provider_name = None
    
    while pending:
        # Wait for the NEXT provider to finish (First Completed)
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            provider = provider_map.get(task)
            if not provider:
                continue

            try:
                raw_result = await task
            except Exception as exc:
                logger.debug(f"Provider task failed for {getattr(provider, 'name', 'Unknown')}: {exc}")
                continue

            lyrics, metadata, word_synced = _normalize_provider_result(raw_result)
            lyrics = _apply_instrumental_marker(lyrics, metadata)

            if lyrics:
                await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata, word_synced=word_synced)
                logger.info(f"Saved lyrics using {provider.name} (Priority {provider.priority})")             
                
                # LINE-SYNC selection: Use pure priority (no boost)
                # Word-sync will be loaded independently via backfill reload (_load_from_db)
                # which now uses dual selection (line-sync from Spotify, word-sync from NetEase/Musixmatch)
                if provider.priority < best_priority:
                    best_priority = provider.priority
                    best_result = lyrics
                    best_provider_name = provider.name
                    logger.info(f"New best result now from {provider.name} (priority {provider.priority})")

                # Case A: High Quality provider (priority 1-2) finished – return immediately for UX
                if provider.priority <= 2:
                    current_song_provider = provider.name
                    if pending:
                        _save_all_results_background(artist, title, pending, provider_map, timeout=LYRICS.get("background_timeout_high_quality", 8.0))
                    return best_result
        
        if best_result and pending:
            high_priority_pending = any(provider_map[t].priority <= 2 for t in pending)
            
            if not high_priority_pending:
                if best_provider_name:
                    current_song_provider = best_provider_name
                logger.info("No high quality providers pending. Returning best current lyrics.")
                _save_all_results_background(artist, title, pending, provider_map, timeout=LYRICS.get("background_timeout_low_quality", 5.0))
                return best_result

            # Case B: Low-quality provider finished first; allow a grace window for upgrades
            grace_period = LYRICS.get("smart_race_timeout", 3.0)
            logger.info(f"Waiting up to {grace_period}s for a high quality upgrade before returning {best_priority}.")
            try:
                done_hq, pending = await asyncio.wait(
                    pending,
                    timeout=grace_period,
                    return_when=asyncio.FIRST_COMPLETED
                )
            except Exception as exc:
                logger.debug(f"Grace wait interrupted: {exc}")
                done_hq = set()

            if done_hq:
                for task in done_hq:
                    provider = provider_map.get(task)
                    if not provider:
                        continue

                    try:
                        raw_result = await task
                    except Exception as exc:
                        logger.debug(f"Provider task failed during grace window ({getattr(provider, 'name', 'Unknown')}): {exc}")
                        continue

                    lyrics, metadata, word_synced = _normalize_provider_result(raw_result)
                    lyrics = _apply_instrumental_marker(lyrics, metadata)

                    if lyrics:
                        await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata, word_synced=word_synced)
                        logger.info(f"Grace window got lyrics from {provider.name} (Priority {provider.priority})")

                        # LINE-SYNC selection: Use pure priority (no boost)
                        # Word-sync will be loaded independently via backfill reload
                        if provider.priority < best_priority:
                            best_priority = provider.priority
                            best_result = lyrics
                            best_provider_name = provider.name
                            logger.info(f"Grace window upgraded best result to {provider.name} (priority {provider.priority})")

                        if provider.priority <= 2:
                            current_song_provider = provider.name
                            if pending:
                                _save_all_results_background(
                                    artist,
                                    title,
                                    pending,
                                    provider_map,
                                    timeout=LYRICS.get("background_timeout_high_quality", 8.0)
                                )
                            return best_result

                # Continue loop to keep waiting for the remaining providers after processing grace tasks
                continue
            else:
                if best_provider_name:
                    current_song_provider = best_provider_name
                logger.info("Grace period expired with no upgrade, returning backup lyrics.")
                _save_all_results_background(artist, title, pending, provider_map, timeout=LYRICS.get("background_timeout_low_quality", 5.0))
                return best_result

    if best_provider_name:
        current_song_provider = best_provider_name
    return best_result

# ==========================================
# Helper Functions (Unchanged)
# ==========================================

def _find_current_lyric_index(delta: Optional[float] = None) -> int:
    """
    Returns index of current lyric line based on song position.
    
    Args:
        delta: Optional manual override for latency compensation.
               If None, reads from settings dynamically.
    """
    if current_song_lyrics is None or current_song_data is None:
        return -1
    
    # Read latency compensation dynamically from settings (allows hot-reload)
    # FIX: Previously used static LATENCY_COMPENSATION which didn't update on settings change
    general_latency = LYRICS.get("display", {}).get("latency_compensation", 0.0)
    
    # Use delta if provided (manual override), otherwise use setting
    base_delta = delta if delta is not None else general_latency
    
    # Adaptive latency compensation: Use separate compensation for Spotify-only mode
    # This helps lyrics sync correctly when using Spotify API as primary source (e.g., HAOS)
    source = current_song_data.get("source", "")
    
    if source == "spotify":
        # Spotify-only mode: Use configurable spotify_latency_compensation
        # Default -0.5s means lyrics appear 500ms later to compensate for API polling latency
        # Users on HAOS or with fast connections may want to adjust this
        adaptive_delta = LYRICS.get("display", {}).get("spotify_latency_compensation", -0.5)
    elif source == "spicetify":
        # Spicetify mode: Use configurable spicetify_latency_compensation
        # Default 0.0s since Spicetify provides real-time position via WebSocket (like Windows SMTC)
        adaptive_delta = LYRICS.get("display", {}).get("spicetify_latency_compensation", 0.0)
    elif source == "audio_recognition":
        # Audio recognition: Use configurable audio_recognition_latency_compensation
        # Positive = lyrics earlier, Negative = lyrics later
        adaptive_delta = LYRICS.get("display", {}).get("audio_recognition_latency_compensation", 0.0)
    else:
        # Normal mode (Windows Media, hybrid): Use base delta
        adaptive_delta = base_delta
    
    position = current_song_data.get("position", 0)
    
    # 1. Before first lyric
    if position + adaptive_delta < current_song_lyrics[0][0]:
        return -2
        
    # 2. After last lyric
    last_lyric_time = current_song_lyrics[-1][0]
    if position + adaptive_delta > last_lyric_time + 9.0: # End song after 9s
        return -3
    
    # 3. Find current line
    for i in range(len(current_song_lyrics) - 1):
        if current_song_lyrics[i][0] <= position + adaptive_delta < current_song_lyrics[i + 1][0]:
            return i
            
    # 4. If we are at the very last line
    if position + adaptive_delta >= last_lyric_time:
        return len(current_song_lyrics) - 1

    return -1

async def get_timed_lyrics(delta: Optional[float] = None) -> str:
    """Returns just the current line text."""
    await _update_song()
    lyric_index = _find_current_lyric_index(delta)
    if lyric_index == -1: return "Lyrics not found"
    if lyric_index < 0: return "..."
    return current_song_lyrics[lyric_index][1]

async def get_timed_lyrics_previous_and_next() -> tuple:
    """Returns tuple of 6 lines: (prev2, prev1, current, next1, next2, next3)."""
    
    def safe_get_line(idx):
        if current_song_lyrics and 0 <= idx < len(current_song_lyrics):
            return current_song_lyrics[idx][1] or "♪"
        return ""

    await _update_song()
    
    if current_song_data is None: return "No song playing"
    if current_song_lyrics is None: return "Lyrics not found"
    
    idx = _find_current_lyric_index()

    # Explicit Flag Check (New)
    is_instrumental = False
    
    # 1. Check if the lyrics list itself has a special flag (we can attach this in providers)
    # For now, we improve the text check to be less brittle
    if len(current_song_lyrics) == 1:
        text = current_song_lyrics[0][1].lower().strip()
        # Check for known "Instrumental" markers from providers
        # Expanded list to catch more symbols and common provider placeholders
        if text in ["instrumental", "music only", "no lyrics", "non-lyrical", "♪", "♫", "♬", "(instrumental)", "[instrumental]"]:
            is_instrumental = True
    
    # Note: Instrumental breaks (sections within songs marked with "(Instrumental)", "[Solo]", etc.)
    # are treated as normal lyric lines and will be displayed. They are not filtered out.
    # The frontend will display them as regular lyrics, which is the correct behavior.
            
    # Handle instrumental / intro
    if idx == -1:
        # Look ahead to see what the first lyric is
        return ("", "", "♪", safe_get_line(0), safe_get_line(1), safe_get_line(2))
    
    if idx == -2: # Intro
        return ("", "", "Intro", safe_get_line(0), safe_get_line(1), safe_get_line(2))
        
    if idx == -3: # Outro
        return (safe_get_line(len(current_song_lyrics)-1), "End", "", "", "", "")
    return (
        safe_get_line(idx - 2),
        safe_get_line(idx - 1),
        safe_get_line(idx),
        safe_get_line(idx + 1),
        safe_get_line(idx + 2),
        safe_get_line(idx + 3)
    )