# Troubleshooting

Common issues and solutions for SyncLyrics.

## Spotify Authentication

### "Login with Spotify" keeps appearing
- Verify `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` are set correctly
- Check that `SPOTIFY_REDIRECT_URI` matches exactly in:
  - Your `.env` or Docker config
  - Spotify Developer Dashboard (Redirect URIs)
- For Docker/HASS, use your actual host URL, not `127.0.0.1`

### Authentication fails after clicking "Login"
- Clear browser cookies for the SyncLyrics URL
- Check the Spotify Developer Dashboard for the exact redirect URI
- Make sure you're accessing SyncLyrics from the same host as the redirect URI

### HTTPS Required for Remote Access
Spotify OAuth requires either:
- `127.0.0.1` or `localhost` (HTTP works)
- **Any other address must use HTTPS**

For HASS/Docker/remote access, use HTTPS on port 9013:
```
https://<YOUR_IP>:9013/callback
```

---

## Lyrics Not Showing

### No lyrics appear
1. Check if music is actually playing
2. Verify the track has lyrics (instrumental tracks show ♪)
3. Check provider connectivity in logs
4. Try a popular song to verify providers work

### Wrong lyrics / mismatched song
- Provider matched a different version
- Click the provider badge to manually select a different source
- Mark as instrumental if appropriate

### Lyrics out of sync
- Adjust latency compensation in settings
- For word-sync: use +/− buttons in provider modal
- Different sources have different timing quality

---

## Album Art Issues

### No album art
- Check API keys for Last.fm and FanArt.tv
- Verify network connectivity
- Try a mainstream artist/album

### Wrong album art
1. Click album art to open provider modal
2. Go to "Album Art & Images" tab
3. Select the correct image

### Low resolution art
- Enable "Spotify Enhanced" in settings (upgrades to 1400px)
- Check "Min Resolution" setting

---

## Audio Recognition

### No match found
- Increase capture duration (5-10s recommended)
- Lower silence threshold if audio is quiet
- Use backend (loopback) mode for cleaner audio
- Ensure device is capturing actual audio

### HTTPS required for browser mic
Browser security requires HTTPS. Access via `https://localhost:9013` and accept the security warning.

### Wrong device selected
- Open audio source modal
- Check device dropdown for available options
- Backend devices require loopback driver (VB-Cable, WASAPI)

---

## Windows Media Not Detected

### App not appearing as source
- Verify the app supports SMTC (System Media Transport Controls)
- Some apps (MusicBee) require plugins for SMTC
- Check `system.windows.app_blocklist` - you may want to block problematic apps like browsers

### Stuck on previous track
- Check `system.windows.paused_timeout` setting
- Default: 600 seconds (10 minutes) before ignoring paused media

---

## Windows SMTC Limitations

Windows System Media Transport Controls (SMTC) is how SyncLyrics detects media from non-Spotify apps. However, **browser-based media players have significant limitations**.

### Browser Position/Playback Issues

**Symptoms:**
- Progress bar frozen or inaccurate
- Lyrics out of sync despite correct song detection
- Position stays at 0:00 while music plays

**Cause:** Browsers (Chrome, Edge, Comet, Firefox) implement minimal SMTC:
- Play/pause and track info work
- **Position timeline rarely updates** (or never)
- Background tabs may stop updates entirely

| Source | Position Updates | Thumbnails | Recommendation |
|--------|-----------------|------------|----------------|
| Spotify (native) | ✅ Every 1-2s | ✅ | Best choice |
| MusicBee | ✅ Good | ⚠️ Varies | Good |
| Browsers (YouTube) | ❌ Rarely/never | ⚠️ Often fails | Not recommended |
| YouTube Music via Spicetify | ✅ Real-time | ✅ | **Use this instead** |

**Solutions:**
1. **Use native apps** (Spotify, MusicBee) instead of browser players
2. **For YouTube Music**: Install the Spicetify extension for real-time sync
3. **For YouTube videos**: Consider blocking browsers (`system.windows.app_blocklist`)
4. **Audio Recognition**: Use Shazam mode for manual identification

### Thumbnail Extraction

Browser media players often can't provide album art via SMTC:
- WinRT thumbnail API times out (1 second limit)
- Falls back to album art database (iTunes, Last.fm, Spotify)
- YouTube videos with non-music content won't find album art

### Why You Might Want to Block Browsers

The blocklist is **empty by default**, but you may want to add browsers (`chrome`, `msedge`, `firefox`, `brave`) if:
1. Position data is unreliable (lyrics won't sync properly)
2. Thumbnails often fail to extract
3. YouTube video metadata is inconsistent (channel name as artist, video title as song)

To add apps to the blocklist, edit `system.windows.app_blocklist` in settings.

---

## Spicetify Issues

### Extension not connecting
1. Verify SyncLyrics is running on port 9012
2. Check extension is installed: `spicetify config extensions`
3. Look for `[SyncLyrics]` messages in Spotify console (Ctrl+Shift+I)
4. Make sure WebSocket URL matches your server

### Waveform/Spectrum not showing
- Enable the feature in display settings
- Spicetify must be connected (check audio source button)
- Data loads per-song, may take a moment on track change

See [Spicetify Integration](Spicetify%20Integration.md) for setup.

---

## Docker / Home Assistant

### Container won't start
Check logs: `docker logs synclyrics`

Common causes:
- Missing Spotify credentials
- Port already in use (change with `-p 9013:9012`)

### Data not persisting
Mount `/data` volume:
```bash
-v /path/to/data:/data
```

### Can't authenticate from external device
- Set `SPOTIFY_REDIRECT_URI` to your actual access URL (not localhost)
- Ensure Spotify Developer Dashboard has this URL in Redirect URIs

See [Docker Reference](Docker%20Reference.md) for complete Docker documentation.

---

## Performance

### High CPU usage
- Reduce polling frequency in settings
- Disable spectrum/waveform if not needed
- Use Spicetify for more efficient updates

### Slow initial load
- First load fetches and caches data
- Subsequent loads are faster due to caching
