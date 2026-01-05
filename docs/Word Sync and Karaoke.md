# Word Sync and Karaoke

SyncLyrics supports two levels of lyrics synchronization: line-sync and word-sync.

## Line-Sync vs Word-Sync

| Type | Description | Visual |
|------|-------------|--------|
| **Line-sync** | Highlights the current line | Standard scrolling lyrics |
| **Word-sync** | Highlights each word as it's sung | Karaoke-style animation |

## Enabling Word-Sync

1. Click the **stars icon** (✨) in the bottom-left corner
2. Or toggle "Word-Sync Lyrics (Karaoke)" in the settings panel

Word-sync only works when:
- The current song has word-synced data from a provider
- A provider with word-sync support is available

## Word-Sync Providers

| Provider | Format | Quality |
|----------|--------|---------|
| Musixmatch | RichSync | ⭐⭐⭐ Best |
| Spotify | Syllable-level | ⭐⭐ Good |
| NetEase | YRC format | ⭐⭐ Good |

The app automatically uses the best available option. Musixmatch RichSync provides the most accurate word timing.

## Timing Adjustment

If word-sync feels off, you can adjust timing per-song:

1. Click the **provider badge** (shows current lyrics source)
2. Use the **+/−** buttons next to the latency display
3. Adjust in 50ms increments

This offset is saved per-song and persists across sessions.

### Keyboard Shortcuts
- **[** / **]**: Adjust timing by 50ms (when available)

## How It Works

Word-sync uses a "flywheel clock" for smooth animation:
- Interpolates position between server polls
- Handles seek, pause, and playback speed changes
- Snaps to actual position when drift exceeds threshold

This provides fluid animation even with 100ms+ polling intervals.

## Visual Styles

Word-sync supports two visual styles (configurable in settings):
- **Fade**: Gradient sweep across words as they're sung
- **Pop**: Words scale up when active

## Troubleshooting

### Word-sync toggle greyed out
The current song doesn't have word-sync data. Try a different song, or check if your preferred provider has word-sync.

### Words highlighting too early/late
Use the timing adjustment (see above) to offset by ±50ms increments.

### Animation is choppy
- With Spicetify: Should be smooth (~100ms updates)
- Without Spicetify: Limited to ~4s polling, animation interpolates between updates
