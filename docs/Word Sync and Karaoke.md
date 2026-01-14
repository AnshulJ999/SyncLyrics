# Word Sync and Karaoke

SyncLyrics supports two levels of lyrics synchronization: line-sync and word-sync.

## Line-Sync vs Word-Sync

| Type | Description | Visual |
|------|-------------|--------|
| **Line-sync** | Highlights the current line | Standard scrolling lyrics |
| **Word-sync** | Highlights each word as it's sung | Karaoke-style animation |

## Enabling Word-Sync

1. Click the **stars icon** (✨) in the bottom-left corner
2. Or toggle "Word-Sync Lyrics (Karaoke)" in the display settings panel (⚙️) (URL param: `wordSync=true`)

Word-sync only works when:
- The current song has word-synced data from a provider
- A provider with word-sync support is available

## Word-Sync Providers

| Provider | Format | Quality |
|----------|--------|---------|
| Musixmatch | RichSync | ⭐⭐ Good |
| NetEase | YRC format | ⭐⭐ Good |

The app automatically uses the highest priority provider. By default Musixmatch is higher priority than NetEase, but you can adjust this if you want. 

Data quality differs a lot between genres and songs; sometimes NetEase provides better quality than Musixmatch. **So if you find word-sync timing is not correct; please try switching to NetEase (if it has word-sync)**.

## Timing Adjustment

If word-sync feels off, you can adjust timing per-song:

1. Click the **provider badge** (shows current lyrics source)
2. Use the **+/−** buttons next to the latency display
3. Adjust in 50ms increments

This offset is saved per-song and persists across sessions.

If you need to adjust the global latency, please do so in the main app settings.

**Update:** The latency controls are shown directly on the main UI next to the provider badge, when word-sync is ON. So you can simply control it from there. If you wish to hide these, click the provider badge and click 'Hide' next to the latency controls.

### Keyboard Shortcuts
- **[** and **]**: Adjust timing by 50ms (when available)

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

If the word-sync timing is _really_ off; it's mostly like bad data from the provider (this is quite common as word-sync data is hard to synchronize). Try switching providers or using line-sync instead. You can also try deleting the cache and re-fetching fresh lyrics.

## Animation Styles

The default animation style is 'Pop'. You can adjust this by going to the Provider menu (bottom-right icon) and clicking on 'Pop' to cycle it. The available styles are: 

**Pop**: Words scale up when active
**Fade**: Gradient sweep across words as they're sung
**Pop-Fade**: Hybrid animation where both Pop and Fade occur simultaneously.
