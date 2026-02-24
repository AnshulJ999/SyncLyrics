---
description: AI navigation guide for the SyncLyrics codebase, covering both backend (system_utils) and frontend (ES modules)
---

# SyncLyrics Codebase Navigation Guide

## Overview
SyncLyrics is a Python web app that displays synchronized lyrics with album art and artist images. It fetches metadata from Windows Media Session, Spotify API, and GNOME/playerctl.

## Entry Points
- **Backend:** `sync_lyrics.py` → `server.py` (Flask API)
- **Frontend:** `resources/js/main.js` (ES module entry point)

## Key Directories
```
g:\GitHub\SyncLyrics\
├── system_utils/           # Backend modular package
├── providers/              # External API integrations
├── resources/
│   ├── js/
│   │   ├── main.js         # Frontend entry point
│   │   └── modules/        # ES modules (state, api, dom, etc.)
│   ├── css/                # Stylesheets
│   └── templates/          # Jinja2 HTML templates
├── logs/                   # Application logs
└── .agent/workflows/       # AI guides
```

---

# FRONTEND (ES Modules)

## ⚠️ CRITICAL: Module Dependency Hierarchy

The frontend uses ES modules with a **strict tiered import hierarchy**. Violating this causes circular dependencies.

```
Level 0: state.js       ← imports NOTHING (shared state + setters)
Level 1: utils.js       ← imports NOTHING (pure functions)
         api.js         ← imports state
         dom.js         ← imports state, utils
Level 2: settings.js    ← imports state, dom, utils, background
         controls.js    ← imports state, dom, utils, api
         background.js  ← imports state, dom
         slideshow.js   ← imports state
Level 3: provider.js    ← imports state, dom, utils, api, background
Level 4: main.js        ← imports ALL (orchestrator)
```

## Module Responsibilities

| Module | Lines | Purpose |
|--------|-------|---------|
| `state.js` | ~110 | All global state variables + setter functions |
| `utils.js` | ~100 | Pure helpers: `normalizeTrackId`, `sleep`, `formatTime` |
| `api.js` | ~375 | All `/api/*` fetches: `getConfig`, `getCurrentTrack`, `getLyrics` |
| `dom.js` | ~180 | DOM manipulation: `setLyricsInDom`, `showToast`, `updateThemeColor` |
| `settings.js` | ~330 | Settings panel, URL param parsing, `initializeDisplay` |
| `controls.js` | ~470 | Playback controls, progress bar, queue drawer, like button |
| `background.js` | ~405 | Background styles (blur/soft/sharp), visual mode state machine |
| `slideshow.js` | ~100 | Artist image slideshow (currently disabled) |
| `provider.js` | ~680 | Provider modal, album art selection, instrumental marking |
| `main.js` | ~310 | Entry point, `updateLoop`, event listener wiring |

## State Management Pattern

State is centralized in `state.js` using **ES Module live bindings**:

```javascript
// state.js exports mutable variables + setters
export let lastTrackInfo = null;
export function setLastTrackInfo(info) { lastTrackInfo = info; }

// Other modules import and use directly
import { lastTrackInfo, setLastTrackInfo } from './state.js';
```

**KEY INSIGHT:** ES module exports are "live bindings". When `setLastTrackInfo()` updates the value, ALL importers see the new value immediately. This is NOT like CommonJS.

## Main Update Loop (`main.js`)

```javascript
async function updateLoop() {
    while (true) {
        const [trackInfo, data] = await Promise.all([
            getCurrentTrack(),
            getLyrics(updateBackground, updateThemeColor, updateProviderDisplay)
        ]);
        
        // Detect track change
        if (trackChanged) {
            resetVisualModeState();
            setManualVisualModeOverride(false);
            setManualStyleOverride(false);
            // ... update UI
        }
        
        // IMPORTANT: Update state BEFORE using it
        setLastTrackInfo(trackInfo);
        
        // Apply background style priority: Saved > URL > Default
        // ...
        
        await sleep(currentPollInterval);
    }
}
```

## Common Patterns

### 1. Function Callbacks for Cross-Module Communication
```javascript
// In api.js - accepts callbacks to avoid circular imports
export async function getLyrics(updateBackgroundFn, updateThemeColorFn, updateProviderDisplayFn) {
    // ...
    if (updateBackgroundFn) updateBackgroundFn();
}
```

### 2. Slideshow Integration (Dependency Injection)
```javascript
// In main.js - connects modules that can't import each other directly
import { setSlideshowFunctions } from './background.js';
import { startSlideshow, stopSlideshow } from './slideshow.js';
setSlideshowFunctions(startSlideshow, stopSlideshow);
```

### 3. Settings Checkbox Handling
```javascript
// In settings.js - always call ALL these after changing config
applyDisplayConfig();
applySoftMode();
applySharpMode();
updateBackground();  // ← CRITICAL: Actually updates the DOM
updateUrlDisplay();
```

## API Endpoints (Frontend → Backend)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/config` | GET | Load update interval, blur settings |
| `/track` | GET | Current track metadata |
| `/lyrics` | GET | Synced lyrics + provider info |
| `/api/providers` | GET | Available lyric providers |
| `/api/album-art/options` | GET | Album art choices |
| `/api/album-art/preference` | POST/DELETE | Set/clear art preference |
| `/api/background-style` | POST | Save background style |
| `/api/queue` | GET | Playback queue |
| `/api/like-status/{id}` | GET/POST | Like button |

## Background Styles

Three mutually exclusive modes in `displayConfig`:
- `artBackground` = blur (default)
- `softAlbumArt` = medium blur
- `sharpAlbumArt` = no blur

Priority order for style application:
1. **Saved preference** (per-track, from backend)
2. **URL parameters** (e.g., `?sharpAlbumArt=true`)
3. **Default** (blur)

---

# BACKEND (system_utils Package)

## Architecture
The package follows a **tiered dependency hierarchy** to prevent circular imports:

```
Level 0: state.py       ← imports NOTHING from package
Level 1: helpers.py     ← imports state
         image.py       ← imports state
Level 2: gnome.py       ← imports state, helpers
         album_art.py   ← imports state, helpers, image
Level 3: artist_image.py ← imports state, helpers, album_art
Level 4: windows.py     ← imports state, helpers, image, album_art, artist_image
         spotify.py     ← imports state, helpers, image, album_art, artist_image
Level 5: metadata.py    ← imports ALL above (orchestrator)
Level 6: __init__.py    ← re-exports everything for backward compatibility
```

## Module Responsibilities

| Module | Lines | Purpose |
|--------|-------|---------| 
| `state.py` | ~150 | Singleton locks, caches, semaphores, constants |
| `helpers.py` | ~200 | Pure utilities: `create_tracked_task`, `sanitize_folder_name` |
| `image.py` | ~200 | Image I/O, color extraction |
| `gnome.py` | ~80 | Linux: `_get_current_song_meta_data_gnome` |
| `album_art.py` | ~900 | Album art DB management |
| `artist_image.py` | ~750 | Artist image DB management |
| `windows.py` | ~350 | Windows Media Session |
| `spotify.py` | ~600 | Spotify API integration |
| `metadata.py` | ~460 | Main orchestrator |

## Key Functions
```python
from system_utils import get_current_song_meta_data
result = await get_current_song_meta_data()  # Returns dict

from system_utils import _art_update_lock, _meta_data_lock
async with _art_update_lock:  # Prevents album art flicker
    ...
```

---

# Gotchas for AI

## Frontend
1. **Always call `updateBackground()` after changing display config** - `applyDisplayConfig()` alone doesn't update the background layer
2. **`setLastTrackInfo()` must be called BEFORE background logic** - Otherwise background.js reads stale data
3. **ES module live bindings work** - Don't pass values as parameters when you can import directly
4. **settings.js imports from background.js** - Not the other way around
5. **slideshow.js is disabled** - `startSlideshow()` has `return;` at the top

## Backend
1. **Never duplicate asyncio.Lock()** - All locks are in `state.py` as singletons
2. **Import from `system_utils` not submodules** - Use `from system_utils import X`
3. **ACTIVE_INTERVAL etc. are in `state.py`** - Not in `config.py`
4. **Lazy imports in helpers.py** - `_log_app_state` uses lazy import to avoid circular deps

## General
1. **CRLF line endings** - This is a Windows project, files use CRLF not LF
2. **Platform-specific modules** - `windows.py` only works on Windows, `gnome.py` on Linux
3. **The old `lyrics.js` is backed up as `lyrics OLD.js`** - Do not use it, reference only

