# FAQ

Common questions about SyncLyrics.

## Spotify

### Do I need Spotify Premium?
No, but some features work better with Premium:
- Free accounts have limited API access
- Playback controls require an active Spotify device

### Can I use Apple Music / YouTube Music?
Not directly. However, you can use **Audio Recognition** to identify songs from any source playing through your speakers or microphone.

However any music playing on your Windows device can be recognized via the SMTC integration.

### What's the difference between Spotify API and Spicetify?
| Aspect | Spotify API | Spicetify |
|--------|-------------|-----------|
| Position updates | Every 2-4 seconds | Every ~100ms |
| Waveform/Spectrum | ❌ | ✅ |
| Queue with autoplay | ❌ | ✅ |
| Setup | Just credentials | Extension install |

Spicetify requires the Spotify Desktop app but provides much better sync.

## Lyrics

### Why are lyrics missing for some songs?
- The song may be instrumental (marked with ♪)
- None of the 5 providers have lyrics for it
- Try clicking the provider badge to manually select a source

### Why are lyrics out of sync?
Adjust latency compensation:
1. Click the provider badge
2. Use +/− buttons to adjust timing
3. For word-sync: use [ and ] keyboard shortcuts

## Setup

### Does this work offline?
Partially. Cached lyrics and album art work offline, but:
- Spotify API requires internet
- Audio recognition requires internet
- New songs need provider access

### Can I use this on a tablet dashboard?
Yes! Common setups:
- HASS addon with iframe card
- Direct URL with `?minimal=true` parameter
- Portrait/landscape both supported

### How do I use this in OBS?
Add a Browser Source with:
- URL: `http://localhost:9012/?minimal=true&hideControls=true`
- Width/Height: Your preference
- Custom CSS: Optional transparency adjustments

## Technical

### Why is CPU usage high?
- Disable waveform/spectrum if not using Spicetify
- Reduce polling frequency in settings
- Spicetify actually reduces CPU vs API polling

### Where is data stored?
| Data | Location |
|------|----------|
| Lyrics | `lyrics_database/` |
| Album art | `album_art_database/` |
| Settings | `settings.json` |
| Spotify tokens | `.cache` or configured path |

### Can I migrate my lyrics database?
Yes, just copy the `lyrics_database/` folder to your new installation.
