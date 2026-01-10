# Latency Tuning Guide

This guide explains how to calibrate lyrics timing so they sync perfectly with your music.

## Understanding Latency Compensation

**Latency compensation** adjusts when lyrics appear relative to the music. Think of it as shifting the lyrics forward or backward in time.

### The Sign Convention

| Value | Effect | When to Use |
|-------|--------|-------------|
| **Positive** (+0.5s) | Lyrics appear **EARLIER** | If lyrics are **behind** the music |
| **Negative** (-0.5s) | Lyrics appear **LATER** | If lyrics are **ahead of** the music |

> **Memory trick**: Positive = Plus time = lyrics start sooner. Negative = Minus time = lyrics start later.

---

## How It Works

The app calculates which lyric to show using this formula:

```
effective_position = current_position + latency_compensation
```

**Example:**
- Song position: 30.0 seconds  
- Latency compensation: -0.5 seconds  
- Effective position: 29.5 seconds

**What happens:** We display the lyric for 29.5s when the song is at 30.0s.  

**Result:** The lyric appears 0.5s **later** than its timestamp (lyrics lag behind the music).

---

## The Six Latency Settings

SyncLyrics has six separate latency settings because different audio sources have different delays:

### 1. General Latency (`latency_compensation`)
- **Default**: -0.1s
- **Used for**: Windows Media (SMTC), fallback sources
- **Why**: Windows apps report position almost instantly

### 2. Spotify API Latency (`spotify_latency_compensation`)
- **Default**: -0.5s
- **Used for**: Spotify API polling (Docker, HASS without Spicetify)
- **Why**: Spotify API has network delay; by the time position arrives, music has advanced ~500ms

### 3. Spicetify Latency (`spicetify_latency_compensation`)
- **Default**: 0.0s
- **Used for**: Spicetify WebSocket (real-time updates)
- **Why**: Spicetify provides near-instant position updates

### 4. Audio Recognition Latency (`audio_recognition_latency_compensation`)
- **Default**: +0.1s
- **Used for**: Shazam/ACRCloud recognition
- **Why**: Recognition starts after audio plays; lyrics need to appear slightly earlier

### 5. Music Assistant Latency (`music_assistant_latency_compensation`)
- **Default**: 0.0s
- **Used for**: Music Assistant server (network streaming)
- **Why**: MA players have variable latency; adjust based on your player/network setup
- **Common adjustment**: Try `-0.3` to `-0.5` if lyrics appear too early

### 6. Word-Sync Latency (`word_sync_latency_compensation`)
- **Default**: -0.1s
- **Used for**: Word-by-word karaoke highlighting
- **Why**: Fine-tuning word transitions; additive to source latency

---

## Word-Sync: How Offsets Stack

For word-sync (karaoke mode), multiple offsets are combined:

```
total_offset = source_latency + word_sync_latency + provider_offset + song_offset
```

| Component | Source | Adjustable? |
|-----------|--------|-------------|
| **Source latency** | Based on source (Spotify/Spicetify/Windows) | Yes (in Settings) |
| **Word-sync latency** | Global word-sync adjustment | Yes (in Settings) |
| **Provider offset** | Internal per-provider tuning | Yes |
| **Song offset** | Per-song adjustment via +/- buttons | Yes (per song) |

---

## Per-Song Adjustment

If a specific song is out of sync, you can adjust it without changing global settings:

1. Click the **provider badge** (shows current lyrics source)
2. Use the **+** and **−** buttons to adjust timing
3. Adjustments are in **50ms increments**
4. Settings are **saved per song** and persist across sessions

**Keyboard shortcuts:**
- `[` = Decrease offset (lyrics later)
- `]` = Increase offset (lyrics earlier)

---

## Troubleshooting Guide

### Lyrics appear BEFORE the singer sings
**Problem**: Lyrics are ahead of the music  
**Solution**: Use a **more negative** value (e.g., `-0.3` → `-0.5`)

### Lyrics appear AFTER the singer sings
**Problem**: Lyrics are behind the music  
**Solution**: Use a **less negative** or **positive** value (e.g., `-0.5` → `-0.2`)

### Word-sync feels slightly off, but line-sync is fine
**Problem**: Word highlighting is early/late within the line  
**Solution**: Adjust `word_sync_latency_compensation` separately

### Only ONE song is out of sync
**Problem**: Global settings work for most songs but not this one  
**Solution**: Use the per-song +/- buttons (don't change global settings)

---

## Recommended Starting Values

| Setup | Source Setting | Recommended |
|-------|----------------|-------------|
| Windows + Spotify Desktop | Spicetify | 0.0s |
| Windows + Spotify Desktop | Windows Media | -0.1s |
| Docker/HASS | Spotify API | -0.5s to -0.8s |
| Audio Recognition | N/A | +0.1s |

> **Tip**: Start with defaults. Only adjust if you notice consistent sync issues across multiple songs.

---

## Settings Location

- **Web UI**: Go to `/settings` → Lyrics section
- **Environment variables**: 
  - `SPOTIFY_LATENCY_COMPENSATION`
  - `SPICETIFY_LATENCY_COMPENSATION`
  - etc.

---

## Technical Details

### Line-Sync Formula (Backend)
```python
# lyrics.py line 1744
effective_position = position + adaptive_delta

# Where adaptive_delta is chosen based on source:
# - spotify: spotify_latency_compensation (-0.5)
# - spicetify: spicetify_latency_compensation (0.0)
# - audio_recognition: audio_recognition_latency_compensation (+0.1)
# - music_assistant: music_assistant_latency_compensation (0.0)
# - default: latency_compensation (-0.1)
```

### Word-Sync Formula (Frontend)
```javascript
// wordSync.js line 665-666
totalLatencyCompensation = wordSyncLatencyCompensation 
                         + wordSyncSpecificLatencyCompensation 
                         + providerWordSyncOffset 
                         + songWordSyncOffset;
serverPosition = anchorPosition + elapsed + totalLatencyCompensation;
```

The word-sync system uses a "flywheel clock" that interpolates position between server updates, providing smooth animation even with polling delays.
