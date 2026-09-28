# Update Checks and Usage Stats

Once a day, SyncLyrics contacts `https://sl.anshuljain.net/v1/checkin` to find out whether a newer version is out. By default it also includes a few anonymous usage stats, so we know roughly how many people use SyncLyrics, on what, and which features matter.

Both are on by default and both can be turned off in **Settings > Updates**. With both off, SyncLyrics makes no request at all.

## What's sent

**Update check** (Settings > Updates > Check for Updates):

| Field | Example |
|---|---|
| SyncLyrics version | `2.5.0` |
| Install type | `docker`, `ha_addon`, `exe`, `appimage` or `source` |
| Operating system | `linux`, `windows` or `macos` |

**Anonymous usage stats** (Settings > Updates > Anonymous Usage Stats) add:

| Field | Example |
|---|---|
| Install ID | A random ID created by SyncLyrics, stored in `state.json`. It isn't tied to you, your device or any account; it only lets us count an install once instead of once per day. |
| CPU architecture | `x86_64`, `aarch64` |
| Python version | `3.12.4` |
| Enabled sources | `spotify`, `music_assistant`, ... (names only) |
| Enabled lyrics providers | `lrclib`, `musixmatch`, ... (names only) |

The server also records the **country** the request came from (worked out by Cloudflare), but only when usage stats are on.

## What's never sent

- What you're listening to: songs, artists, albums, lyrics, album art
- Your settings values, API keys, tokens or server addresses
- Anything about your network or other devices

## What's stored

- Your IP address is **not** stored.
- With usage stats on: one row per install (the fields above, plus the first and last day it checked in). An install that hasn't checked in for 90 days is deleted.
- Daily totals: how many checks came in per version, install type and OS.

## Turning it off

- In the app: **Settings > Updates**.
- Docker or `.env`: `UPDATES_CHECK_ENABLED=false` and/or `UPDATES_USAGE_STATS=false`.

Turning off update checks means SyncLyrics won't tell you about new versions. Home Assistant users still get add-on updates from Home Assistant itself.
