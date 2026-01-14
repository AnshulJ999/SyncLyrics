# Audio Recognition

SyncLyrics includes Shazam-like audio recognition for identifying songs when standard media sources aren't available.

## When to Use

Audio recognition is useful when:
- Playing music through a DAW (like Reaper)
- Using a media player without native metadata support
- Playing audio from external devices
- Identifying songs from TV/speakers

## Two Capture Modes

### Backend Mode (System Audio)
Captures audio directly from your system using a loopback device.

**Requirements**:
- Microphone OR Loopback audio device (e.g., VB-Cable, WASAPI loopback)
- Works on HTTP (no HTTPS required)

**Setup**:
1. Install a virtual audio cable or loopback driver or use your microphone
2. Configure your audio to route through the loopback
3. Select the loopback device in SyncLyrics

I've only tested hardware loopback (MOTU M4) but this should work with any loopback. Cannot work without a loopback AKA it does not recognize audio just from your speakers.

It CAN work with any mic / input line on your system that can record audio.

### Frontend Mode (Browser Microphone)
Uses your browser's microphone to capture audio.

**Requirements**:
- HTTPS connection (browsers require secure context for microphone)
- Microphone access permission

**Setup**:
1. Access SyncLyrics via HTTPS (default: `https://localhost:9013`)
2. Accept the browser's microphone permission prompt
3. Select "Browser Mic" in the audio source modal

## Using Audio Recognition

1. Click the **audio source button** (top-right, shows current source)
2. Choose a capture mode:
   - **Quick Start - System Audio**: Uses backend loopback
   - **Quick Start - Browser Mic**: Uses frontend microphone
3. Or select a specific device from the dropdown
4. Click **Start Recognition**

### Recognition Status
- **Idle**: Not running
- **Listening**: Capturing audio
- **Processing**: Analyzing with Shazam
- **Matched**: Song identified

### Advanced Settings
Expand "Advanced Settings" to configure:
- **Recognition Interval**: How often to sample - this can be kept low as it is just the 'gap' between recording captures. 
- **Capture Duration**: How long to record per sample - higher values can lead to more accuracy so a balance is recommended.
- **Latency Offset**: Adjust timing if lyrics are offset
- **Silence Threshold**: Skip recognition when audio is too quiet
- **Verification Cycles**: Number of consecutive matches needed before accepting a new song (default: 2). Set to 1 for instant matching, higher for noisy environments to prevent flickering.

## ACRCloud Fallback

If Shazam fails to identify a song, SyncLyrics can fall back to ACRCloud (optional).

**Setup**:
1. Create account at [console.acrcloud.com](https://console.acrcloud.com/)
2. Create a project and attach the "ACRCloud Music" bucket
3. Set environment variables:
   - `ACRCLOUD_HOST`: Your project host
   - `ACRCLOUD_ACCESS_KEY`: Access key
   - `ACRCLOUD_ACCESS_SECRET`: Access secret
   - `ACRCLOUD_DAILY_LIMIT`: Daily request limit (default: 100)
   - `ACRCLOUD_COOLDOWN`: Seconds between requests (default: 30)

ACRCloud is only called when Shazam returns no match. Results bypass verification (high confidence).

ACRCloud is only enabled if you provide the correct ENV variables; it will not be enabled otherwise. Please reach out if you face any issues. 

## Reaper DAW Integration

SyncLyrics can auto-detect when Reaper is running and can automatically start recognition.

**How it works**:
1. Enable "Reaper Auto-Detect" in settings
2. When Reaper is detected, audio recognition starts automatically
3. Recognition uses your configured loopback device
4. Songs are matched and enriched with Spotify metadata

Similar to Reaper; other apps can be added to auto-detect if required. Please reach out for any help or requests regarding this. 

## Spotify Enrichment

When a song is identified via audio recognition, SyncLyrics attempts to enrich it with Spotify data:
- High-quality album art
- Accurate metadata (artist, album, duration)
- Lyrics from all providers

This gives you full SyncLyrics features even for non-Spotify playback.

## Troubleshooting

### No match found
- Ensure audio is actually playing and audible
- Increase capture duration (6-10s works best)
- Lower silence threshold if audio is quiet
- Try backend mode if frontend mic has poor quality

Shazam does not have all songs in its library, so certain niche songs are not picked up by it. If this is a requirement for you, I recommend trying ACRCloud.

### Wrong song matched
- This can happen with covers or remixes
- May improve with longer capture duration

### HTTPS required for browser mic
Browser security requires HTTPS for microphone access. SyncLyrics auto-generates a self-signed certificate. Access via `https://localhost:9013` and accept the security warning.

### Recognition is slow
- Reduce recognition interval (but increases API usage)
- Shazam API has rate limits; don't go below 3s interval
