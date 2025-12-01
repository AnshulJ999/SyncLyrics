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
from providers.musicxmatch import MusicxmatchProvider
from config import LYRICS, DEBUG, FEATURES, DATABASE_DIR
from logging_config import get_logger

logger = get_logger(__name__)

# Initialize providers
# Priority Order (from settings.json):
# 1. Spotify (Priority 1) - Best for Spotify users
# 2. LRCLib (Priority 2) - Open Source, good quality
# 3. NetEase (Priority 3) - Good coverage
# 4. QQ Music (Priority 4) - Fallback
# 5. Musicxmatch (Priority 5) - Disabled by default
providers = [
    LRCLIBProvider(),      # Priority 1
    SpotifyLyrics(),       # Priority 2
    MusicxmatchProvider(), # Priority 2
    NetEaseProvider(),     # Priority 3
    QQMusicProvider()      # Priority 4
]

LATENCY_COMPENSATION = LYRICS.get("display", {}).get("latency_compensation", 0.1)
current_song_data = None
current_song_lyrics = None
current_song_provider: Optional[str] = None  # Tracks which provider is currently serving lyrics
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
    """Loads lyrics from disk, prioritizing user preference or highest-quality provider available."""
    global current_song_provider
    
    if not FEATURES.get("save_lyrics_locally", False): return None
    
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path): return None
    
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # NEW FORMAT: Multi-provider storage
        if "saved_lyrics" in data and isinstance(data["saved_lyrics"], dict):
            saved_lyrics = data["saved_lyrics"]
            
            # Check for user's preferred provider first
            preferred_provider = data.get('preferred_provider')
            if preferred_provider and preferred_provider in saved_lyrics:
                current_song_provider = preferred_provider
                logger.info(f"Loaded lyrics from Local DB: {preferred_provider} (User Preference)")
                return saved_lyrics[preferred_provider]
            
            # If no preference, find the BEST provider available (lowest priority number = best)
            best_priority = 999
            best_lyrics = None
            best_provider = None
            
            for provider in providers:
                if provider.name in saved_lyrics:
                    if provider.priority < best_priority:
                        best_priority = provider.priority
                        best_lyrics = saved_lyrics[provider.name]
                        best_provider = provider.name
            
            if best_lyrics:
                current_song_provider = best_provider
                logger.info(f"Loaded lyrics from Local DB: {best_provider} (Priority {best_priority})")
                return best_lyrics
        
        # LEGACY FORMAT: Single provider (backward compatibility)
        elif data.get('lyrics') and isinstance(data['lyrics'], list):
            source = data.get('source', 'Unknown')
            current_song_provider = source
            logger.info(f"Loaded lyrics from Local DB (legacy): {source}")
            return data['lyrics']
            
    except Exception as e:
        logger.error(f"Failed to load from Local DB: {e}")
    
    return None

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


def _normalize_provider_result(result: Optional[Any]) -> Tuple[Optional[List[Tuple[float, str]]], Dict[str, Any]]:
    """
    Normalize provider output into a lyrics list and metadata dict.

    This allows new providers to return dictionaries while maintaining backwards
    compatibility with existing ones that return lists.
    """
    if not result:
        return None, {}

    if isinstance(result, list):
        return result, {}

    if isinstance(result, dict):
        lyrics = result.get("lyrics")
        if not isinstance(lyrics, list):
            return None, {}

        metadata = {key: value for key, value in result.items() if key != "lyrics"}
        metadata.setdefault("is_instrumental", False)
        return lyrics, metadata

    return None, {}


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
    Returns True if successful, False otherwise.
    """
    if not FEATURES.get("save_lyrics_locally", False):
        return False
    
    db_path = _get_db_path(artist, title)
    if not db_path:
        return False
    
    async with _db_lock:
        try:
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
            
            logger.info(f"Marked {artist} - {title} as {'instrumental' if is_instrumental else 'NOT instrumental'} (manual)")
            return True
        except Exception as e:
            logger.error(f"Failed to mark instrumental flag: {e}")
            return False

def _normalized_song_key(artist: str, title: str) -> str:
    """Creates a consistent key for tracking per-song background tasks."""
    return f"{artist.strip().lower()}::{title.strip().lower()}"

async def _save_to_db(artist: str, title: str, lyrics: list, source: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Saves found lyrics to disk with multi-provider support (merge mode)."""
    if not FEATURES.get("save_lyrics_locally", False) or not lyrics: return
    
    db_path = _get_db_path(artist, title)
    if not db_path: return

    async with _db_lock:
        try:
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
            
            # Save merged data using atomic write pattern
            # This prevents corruption if app crashes during write:
            # 1. Write to temp file first
            # 2. Use os.replace() to atomically swap (this is atomic on all platforms)
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
                
            logger.info(f"Saved {source} lyrics to DB (now has {len(data['saved_lyrics'])} providers)")
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
                        lyrics, metadata = _normalize_provider_result(raw_result)
                        lyrics = _apply_instrumental_marker(lyrics, metadata)
                        if lyrics:
                            await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata)
                            logger.info(f"Background save complete for {provider.name}")
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
    missing_providers: List[object]
) -> None:
    """Fetches any providers that are missing in the DB while UI uses cached lyrics."""
    song_key = _normalized_song_key(artist, title)
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
                    coro = provider.get_lyrics(artist, title)
                else:
                    coro = asyncio.to_thread(provider.get_lyrics, artist, title)

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
                        lyrics, metadata = _normalize_provider_result(raw_result)
                        lyrics = _apply_instrumental_marker(lyrics, metadata)
                        if lyrics:
                            await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata)
                            logger.info(f"Backfill saved lyrics from {provider.name}")
                            
                            # Check again after saving - if we now have 3 providers, stop
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
            'is_current': bool
        }
    """
    # Check database for cached providers
    saved_providers = _get_saved_provider_names(artist, title)
    
    result = []
    for provider in providers:
        if not provider.enabled:
            continue
            
        result.append({
            'name': provider.name,
            'priority': provider.priority,
            'cached': provider.name in saved_providers,
            'is_current': provider.name == current_song_provider
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
    global current_song_provider, current_song_lyrics
    
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
        if asyncio.iscoroutinefunction(provider_obj.get_lyrics):
            raw_result = await provider_obj.get_lyrics(artist, title)
        else:
            raw_result = await asyncio.to_thread(provider_obj.get_lyrics, artist, title)

        lyrics, metadata = _normalize_provider_result(raw_result)
        lyrics = _apply_instrumental_marker(lyrics, metadata)

        if lyrics:
            # Save to DB with preference
            await _save_to_db(artist, title, lyrics, provider_name, metadata=metadata)
            
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

# ==========================================
# Main Logic
# ==========================================

async def _fetch_and_set_lyrics(target_artist: str, target_title: str):
    """
    Background task helper to fetch lyrics without blocking the UI.
    
    This function runs in the background after _update_song has already
    updated current_song_data and released the lock. This prevents the
    UI from freezing while waiting for internet requests to complete.
    """
    global current_song_lyrics, current_song_data, current_song_provider

    try:
        # Use the global _get_lyrics function to fetch from internet providers
        fetched_lyrics = await _get_lyrics(target_artist, target_title)
        
        # CRITICAL: Check if song is still the same before setting lyrics
        # This prevents stale lyrics from a previous song being displayed
        # if the user skipped to a new song while this fetch was in progress
        if (current_song_data and 
            current_song_data["artist"] == target_artist and 
            current_song_data["title"] == target_title):
            current_song_lyrics = fetched_lyrics
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

    # CRITICAL FIX: Use lock to prevent concurrent updates
    # This ensures only one song update happens at a time, preventing race conditions
    # where multiple calls to _update_song() could interleave and cause wrong lyrics
    # to be displayed for the current song
    async with _update_lock:
        new_song_data = await get_current_song_meta_data()

        # If no song or empty song, clear lyrics
        if new_song_data is None or (not new_song_data["artist"].strip() and not new_song_data["title"].strip()):
            current_song_lyrics = None
            current_song_data = new_song_data
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
                    # Only backfill if we have fewer than 3 providers saved
                    # This prevents unnecessary requests to unreliable providers
                    if len(saved_providers) < 3:
                        missing = [
                            provider
                            for provider in providers
                            if provider.enabled and provider.name not in saved_providers
                        ]
                        if missing:
                            logger.info(f"Backfill triggered for {target_artist} - {target_title} (have {len(saved_providers)}/3 providers, missing: {', '.join(p.name for p in missing)})")
                            _backfill_missing_providers(target_artist, target_title, missing)
                    else:
                        logger.debug(f"Skipping backfill for {target_artist} - {target_title} (already have {len(saved_providers)} providers)")
            else:
                # 2. Try Internet (Smart Race) - BACKGROUND
                # CRITICAL PERFORMANCE FIX: Don't await internet fetch inside lock
                # Starting a background task allows the UI to remain responsive while
                # lyrics are being fetched from providers. The lock is released immediately
                # so other operations can continue, and _fetch_and_set_lyrics will update
                # current_song_lyrics when the fetch completes (if song hasn't changed).
                current_song_lyrics = [(0, "Searching lyrics...")] 
                create_tracked_task(_fetch_and_set_lyrics(target_artist, target_title))
        else:
            # Song hasn't changed, just update the metadata (position, etc.)
            current_song_data = new_song_data

async def _get_lyrics(artist: str, title: str):
    """
    Tries providers to find lyrics.
    
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
                    raw_result = await provider.get_lyrics(artist, title)
                else:
                    raw_result = await asyncio.to_thread(provider.get_lyrics, artist, title)

                lyrics, metadata = _normalize_provider_result(raw_result)
                lyrics = _apply_instrumental_marker(lyrics, metadata)

                if lyrics:
                    logger.info(f"Found lyrics using {provider.name}")
                    await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata)
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
            coro = provider.get_lyrics(artist, title)
        else:
            coro = asyncio.to_thread(provider.get_lyrics, artist, title)
        
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

            lyrics, metadata = _normalize_provider_result(raw_result)
            lyrics = _apply_instrumental_marker(lyrics, metadata)

            if lyrics:
                await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata)
                logger.info(f"Saved lyrics using {provider.name} (Priority {provider.priority})")

                if provider.priority < best_priority:
                    best_priority = provider.priority
                    best_result = lyrics
                    best_provider_name = provider.name
                    logger.info(f"New best result now from {provider.name}")

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

                    lyrics, metadata = _normalize_provider_result(raw_result)
                    lyrics = _apply_instrumental_marker(lyrics, metadata)

                    if lyrics:
                        await _save_to_db(artist, title, lyrics, provider.name, metadata=metadata)
                        logger.info(f"Grace window got lyrics from {provider.name} (Priority {provider.priority})")

                        if provider.priority < best_priority:
                            best_priority = provider.priority
                            best_result = lyrics
                            best_provider_name = provider.name
                            logger.info(f"Grace window upgraded best result to {provider.name}")

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

def _find_current_lyric_index(delta: float = LATENCY_COMPENSATION) -> int:
    """Returns index of current lyric line based on song position."""
    if current_song_lyrics is None or current_song_data is None:
        return -1
    
    # Adaptive latency compensation: Use higher compensation for Spotify-only mode
    # This helps lyrics appear earlier when using Spotify API as primary source
    source = current_song_data.get("source", "")
    is_spotify_only = (source == "spotify")  # Spotify-only mode (not hybrid, not windows_media)
    
    if is_spotify_only:
        # Spotify-only mode: Use -0.5s compensation (lyrics appear 500ms later)
        # This compensates for API polling latency and network delay
        # Negative value means lyrics appear after the actual timestamp
        adaptive_delta = -0.5
    else:
        # Normal mode (Windows Media or hybrid): Use default compensation
        adaptive_delta = delta if delta != LATENCY_COMPENSATION else LATENCY_COMPENSATION
    
    position = current_song_data.get("position", 0)
    
    # 1. Before first lyric
    if position + adaptive_delta < current_song_lyrics[0][0]:
        return -2
        
    # 2. After last lyric
    last_lyric_time = current_song_lyrics[-1][0]
    if position + adaptive_delta > last_lyric_time + 6.0: # End song after 6s
        return -3
    
    # 3. Find current line
    for i in range(len(current_song_lyrics) - 1):
        if current_song_lyrics[i][0] <= position + adaptive_delta < current_song_lyrics[i + 1][0]:
            return i
            
    # 4. If we are at the very last line
    if position + adaptive_delta >= last_lyric_time:
        return len(current_song_lyrics) - 1

    return -1

async def get_timed_lyrics(delta: int = 0) -> str:
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