# Adding New Metadata Sources

This guide explains how to add support for a new music source (like Music Assistant, Jellyfin, Apple Music, etc.) to SyncLyrics using the plugin-based architecture.

## Quick Start

Adding a new source is simple:

1. Create a new file: `system_utils/sources/your_source.py`
2. Subclass `BaseMetadataSource`
3. Implement `get_config()`, `capabilities()`, `get_metadata()`
4. Add settings to `settings.py`
5. Restart SyncLyrics - your source auto-registers!

## Minimal Example

```python
from .base import BaseMetadataSource, SourceConfig, SourceCapability

class MySource(BaseMetadataSource):
    
    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="my_source",           # Internal ID
            display_name="My Music App", # UI name
            platforms=["Windows", "Linux", "Darwin"],
            default_enabled=False,       # Disabled by default
            default_priority=5,          # Lower = higher priority
        )
    
    @classmethod
    def capabilities(cls) -> SourceCapability:
        return SourceCapability.METADATA  # Just metadata, no controls
    
    async def get_metadata(self):
        # Fetch from your API
        track = await self._fetch_current_track()
        if not track:
            return None
        
        return {
            "artist": track["artist"],
            "title": track["title"],
            "is_playing": track["playing"],
            "source": "my_source",  # Must match config.name
        }
```

## Required Methods

### `get_config()` → `SourceConfig`

Returns static configuration for your source:

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Internal ID (lowercase, underscores, e.g., "music_assistant") |
| `display_name` | str | Human-readable name for UI (e.g., "Music Assistant") |
| `platforms` | list | Supported OS: "Windows", "Linux", "Darwin" |
| `default_enabled` | bool | Whether enabled by default (False for sources needing config) |
| `default_priority` | int | Priority (lower = checked first, e.g., 0-10) |
| `paused_timeout` | int | Seconds before paused source expires (default: 600) |

### `capabilities()` → `SourceCapability`

Returns what your source can do. Combine with `|`:

```python
# Metadata only
return SourceCapability.METADATA

# Full playback controls
return (
    SourceCapability.METADATA |
    SourceCapability.PLAYBACK_CONTROL |
    SourceCapability.SEEK
)
```

Available capabilities:
- `METADATA` - Can fetch track info
- `PLAYBACK_CONTROL` - Can play/pause/next/prev
- `SEEK` - Can seek to position
- `ALBUM_ART` - Provides album art URL directly
- `DURATION` - Provides track duration
- `QUEUE` - Can provide playback queue

### `get_metadata()` → `dict` or `None`

Returns current track info. Returns `None` if nothing playing.

**Required fields:**
```python
{
    "artist": "Artist Name",
    "title": "Song Title",
    "is_playing": True,           # True if actively playing
    # Note: "source" is auto-set to match your config.name
}
```

**Recommended fields:**
```python
{
    "track_id": "Artist_Title",   # Normalized for change detection (auto-generated if missing)
    "album": "Album Name",        # Can be None
    "position": 45.5,             # Seconds into track
    "duration_ms": 240000,        # Track length in milliseconds
    "album_art_url": "https://...", # Will be cached to local DB
    "colors": ("#24273a", "#363b54"), # Extracted if missing
}
```

**Optional fields (for enhanced features):**
```python
{
    "id": "spotify_track_id",      # Enables Like button
    "artist_id": "spotify_artist_id", # Enables Visual Mode
    "url": "https://open.spotify.com/track/...", # External link
    "last_active_time": 1704067200.0, # For paused timeout
}
```

## Optional: Playback Controls

If your source supports playback, implement these and include `PLAYBACK_CONTROL` in capabilities:

```python
async def toggle_playback(self) -> bool:
    """Toggle play/pause. Returns True if successful."""
    ...
    
async def next_track(self) -> bool:
    """Skip to next. Returns True if successful."""
    ...
    
async def previous_track(self) -> bool:
    """Skip to previous. Returns True if successful."""
    ...
    
async def seek(self, position_ms: int) -> bool:
    """Seek to position in ms. Returns True if successful."""
    ...
```

## Optional: Availability Check

Override `is_available()` to check platform-specific requirements:

```python
def is_available(self) -> bool:
    # Check if we're on the right platform
    if platform.system() != "Linux":
        return False
    
    # Check if dependencies are installed
    try:
        subprocess.run(["playerctl", "--version"], capture_output=True, check=True)
        return True
    except:
        return False
```

## Adding Settings

> **Note:** Adding settings to `settings.py` is **optional**. Your plugin will work using its default values from `SourceConfig`. Users can also configure via `settings.json` directly.
>
> Settings entries are only needed if you want:
> - Your source to appear in the Settings UI
> - Your source to be a "bundled" first-class plugin

Add entries to `settings.py` in the `_definitions` dict:

```python
# Media Source
"media_source.your_source.enabled": Setting(
    "Your Source", bool, False, True, "Media",
    "Enable Your Source", "switch"
),
"media_source.your_source.priority": Setting(
    "Priority", int, 5, False, "Media",
    "Source priority (lower = first)", "number"
),

# System (for timeout)
"system.your_source.paused_timeout": Setting(
    "Paused Timeout", int, 600, False, "System",
    "Seconds before source expires (0=forever)", "number"
),
```

## Enrichment

Your source automatically gets full enrichment:
- **Album Art DB** - Art is cached locally
- **Color Extraction** - Colors extracted from art
- **Artist Images** - Background images fetched
- **Background Tasks** - Progressive enhancement

Just return an `album_art_url` and enrichment handles the rest!

## Tips

1. **Use async properly** - Run blocking I/O in executor:
   ```python
   loop = asyncio.get_running_loop()
   result = await loop.run_in_executor(None, blocking_function)
   ```
Ensure all source-specific operations are non-blocking and don't interfere with the main loop.
2. **Handle errors gracefully** - Return `None`, don't raise:
   ```python
   async def get_metadata(self):
       try:
           return await self._fetch()
       except Exception as e:
           logger.debug(f"Fetch failed: {e}")
           return None
   ```

3. **Cache results** - Avoid repeated API calls:
   ```python
   def __init__(self):
       super().__init__()
       self._cache = None
       self._cache_time = 0
   ```

4. **Track active time** - For paused timeout:
   ```python
   if result.get("is_playing"):
       self._last_active_time = time.time()
   result["last_active_time"] = self._last_active_time
   ```

## Testing

```bash
# Check your source is discovered
python -c "from system_utils.sources import get_all_source_classes; print(get_all_source_classes())"

# Check it's available
python -c "from system_utils.sources.your_source import YourSource; print(YourSource().is_available())"

# Check settings are loaded
python -c "from config import conf; print(conf('media_source.your_source.enabled'))"
```

## Example: Full Source

See `system_utils/sources/linux.py` for a complete example with:
- Platform checks
- Subprocess-based metadata fetching
- Full playback controls
- Proper error handling
