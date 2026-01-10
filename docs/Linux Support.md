# Linux Support

SyncLyrics natively supports Linux through the **MPRIS (Media Player Remote Interfacing Specification)** standard using `playerctl`.

## Requirements

- Linux operating system (any desktop environment)
- `playerctl` installed

## Installation

### Ubuntu / Debian
```bash
sudo apt install playerctl
```

### Fedora
```bash
sudo dnf install playerctl
```

### Arch Linux
```bash
sudo pacman -S playerctl
```

### openSUSE
```bash
sudo zypper install playerctl
```

## Supported Desktop Environments

`playerctl` works with **all** Linux desktop environments:

- ✅ GNOME
- ✅ KDE Plasma
- ✅ XFCE
- ✅ Cinnamon
- ✅ MATE
- ✅ LXQt
- ✅ Budgie
- ✅ i3 / Sway (tiling WMs)
- ✅ Hyprland

Both **Wayland** and **X11** are supported.

## Supported Players

Any media player that implements MPRIS D-Bus interface:

- Spotify
- VLC
- Firefox (audio/video)
- Rhythmbox
- Clementine
- Audacious
- MPV (with mpv-mpris plugin)
- Chromium-based browsers
- And many more...

## Features

| Feature | Status |
|---------|--------|
| Track metadata (artist, title, album) | ✅ |
| Playback position | ✅ |
| Duration | ✅ |
| Album art | ✅ |
| Play/Pause control | ✅ |
| Next/Previous track | ✅ |
| Seek to position | ✅ |
| Auto-enrichment (colors, artist images) | ✅ |

## Configuration

The Linux source is **enabled by default** on Linux systems.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `media_source.linux.enabled` | `true` | Enable/disable Linux source |
| `media_source.linux.priority` | `1` | Priority (lower = higher priority) |
| `system.linux.paused_timeout` | `600` | Seconds before paused source expires (0 = never) |

## Troubleshooting

### playerctl not found

If you see "playerctl not installed" in logs:
```bash
# Verify installation
playerctl --version

# If not installed, install it (see above)
```

### No players found

If no metadata appears:
```bash
# Check if any player is detected
playerctl -l

# Check player status
playerctl status
```

If no players are listed:
1. Ensure a media player is running and playing music
2. Verify the player supports MPRIS (most modern players do)
3. For Wayland compositors, ensure `playerctld` daemon is running:
   ```bash
   playerctld daemon &
   ```

### Position not updating

Some players don't push position updates frequently. This is a player limitation, not SyncLyrics.

## playerctld Daemon

For consistent player detection (especially on Wayland), run the playerctld daemon at startup:

```bash
# Start daemon (add to startup script)
playerctld daemon &

# Or via systemd user unit
systemctl --user enable playerctld
systemctl --user start playerctld
```

This ensures the most recently active player is always tracked correctly.
