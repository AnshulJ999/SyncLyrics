# FAQ

Common questions about SyncLyrics.

## Spotify

### Do I need Spotify Premium?
No, but some features work better with Premium:
- Free accounts have limited API access
- Playback controls require an active Spotify device

### Can I use Apple Music / YouTube Music / TIDAL?
Yes, in a few ways:

- **Any player on the same Windows PC or Mac** is picked up through Windows Media (SMTC) or macOS Now Playing.
- **YouTube Music**: the [Pear Desktop](https://github.com/pear-devs/pear-desktop) app has its own source (Settings > Media).
- **TIDAL**: the [tidal-hifi](https://github.com/Mastermindzh/tidal-hifi) desktop app has its own source (Settings > Media).
- **Music on a phone or another device**: [Now Playing Input](Now%20Playing%20Input.md) lets a phone automation, Home Assistant or a script tell SyncLyrics what's playing.
- **Anything else**: **Audio Recognition** identifies whatever is playing through your speakers or microphone.

**YouTube Music (browser)**: Windows Media detection works, but has limitations:
- Position updates are unreliable (lyrics may drift out of sync)
- Thumbnails often fail to load
- Better option: use the [Pear Desktop](https://github.com/pear-devs/pear-desktop) app and its SyncLyrics source for real-time sync

**YouTube videos**: Not recommended for lyrics sync:
- Video metadata (channel name, video title) doesn't match song databases
- Position rarely updates from browsers
- Consider blocking browsers in `system.windows.app_blocklist`

See [Troubleshooting - Windows SMTC Limitations](Troubleshooting.md#windows-smtc-limitations) for details.

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
- None of the 5 providers have synced lyrics for it
- Try clicking the provider badge to manually select a source

### Why are lyrics out of sync?
Adjust latency compensation:
1. Click the provider badge
2. Use +/− buttons to adjust timing
3. For word-sync: use [ and ] keyboard shortcuts

On radio through Music Assistant, lyrics can't be synced at all, because stations don't report how far into the song they are. Use Audio Recognition instead; see [Music Assistant: Radio](Music%20Assistant.md#radio).

## Setup

### Does this work offline?
Partially. Cached lyrics and album art work offline, but:
- Spotify API requires internet
- Audio recognition requires internet
- New songs need provider access

Full offline support may work once the lyrics/art have been cached and if you use an offline music source. This is untested but I plan to add support for full offline usage. 

### Does SyncLyrics contact the internet by itself?
Besides fetching lyrics and art, once a day it checks whether a newer version is out, and by default includes a few anonymous usage stats. It never sends what you listen to or your settings. Both can be turned off in **Settings > Updates**. Details: [Usage Stats](Usage%20Stats.md).

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
| App state (install ID, update check, panels seen) | `state.json` |
| Spotify tokens | `.cache` or configured path |

### Can I migrate my lyrics database?
Yes, just copy the `lyrics_database/` folder to your new installation.
