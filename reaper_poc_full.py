import asyncio
import sounddevice as sd
import numpy as np
from shazamio import Shazam
from pydub import AudioSegment
from io import BytesIO
import sys
import os
import time

# Ensure we can import from the current directory
sys.path.append(os.getcwd())

# Import your actual app modules
import lyrics
from config import LYRICS

# --- CONFIGURATION ---
DEVICE_ID = 3        # MOTU Loopback (MME)
DURATION = 4         # Capture duration (seconds)
SAMPLE_RATE = 44100  # Sample rate
INTERVAL = 1         # Wait time between loops

async def capture_audio():
    """Captures audio from the loopback device."""
    print(f"\n🎤 Listening... ({DURATION}s)")
    try:
        # Run blocking recording in executor to avoid freezing the loop
        loop = asyncio.get_running_loop()
        recording = await loop.run_in_executor(None, lambda: sd.rec(
            int(DURATION * SAMPLE_RATE), 
            samplerate=SAMPLE_RATE, 
            channels=2, 
            dtype='int16', 
            device=DEVICE_ID
        ))
        sd.wait()
        return recording
    except Exception as e:
        print(f"❌ Audio capture failed: {e}")
        return None

async def identify_song(recording):
    """Identifies the song using ShazamIO."""
    try:
        # Convert to WAV for Shazam
        audio_segment = AudioSegment(
            recording.tobytes(), 
            frame_rate=SAMPLE_RATE,
            sample_width=2, 
            channels=2
        )
        buf = BytesIO()
        audio_segment.export(buf, format="wav")
        
        # Send to Shazam
        shazam = Shazam()
        result = await shazam.recognize(buf.getvalue())
        
        if not result.get('matches'):
            return None
            
        track = result['track']
        match = result['matches'][0]
        
        return {
            "title": track['title'],
            "artist": track['subtitle'],
            "offset": match['offset'],
            "skew": match.get('time_skew', 0)
        }
    except Exception as e:
        print(f"❌ Recognition failed: {e}")
        return None

async def run_full_stack_poc():
    print("\n" + "="*50)
    print("🚀 REAPER INTEGRATION LIVE LOOP")
    print("="*50)
    print("Press Ctrl+C to stop.")
    
    last_song_key = None
    cached_lyrics = None
    
    while True:
        try:
            # 1. Capture
            capture_start_time = time.time()  # Track start time for latency compensation
            recording = await capture_audio()
            if recording is None:
                await asyncio.sleep(INTERVAL)
                continue

            # 2. Identify
            song = await identify_song(recording)
            
            if not song:
                print("❌ No music detected.")
                await asyncio.sleep(INTERVAL)
                continue
                
            artist = song['artist']
            title = song['title']
            offset = song['offset']
            
            # --- LATENCY COMPENSATION ---
            # Calculate how much time passed since we started recording
            # This accounts for: Recording Duration + Processing Time + Network Latency
            time_now = time.time()
            latency = time_now - capture_start_time
            adjusted_offset = offset + latency
            
            # 3. Fetch Lyrics (Only if song changed)
            song_key = f"{artist} - {title}"
            
            if song_key != last_song_key:
                print(f"\n🎵 NEW SONG: {song_key}")
                print(f"📥 Fetching lyrics...")
                
                # Call actual app logic
                cached_lyrics = await lyrics._get_lyrics(artist, title)
                last_song_key = song_key
                
                if not cached_lyrics:
                    print("❌ Lyrics not found.")
            
            # 4. Display Sync
            if cached_lyrics:
                # Find current line using ADJUSTED offset
                current_text = "..."
                next_text = ""
                
                for i, (time_sec, text) in enumerate(cached_lyrics):
                    if time_sec <= adjusted_offset:
                        current_text = text
                        if i + 1 < len(cached_lyrics):
                            next_text = cached_lyrics[i+1][1]
                    else:
                        break
                
                print("\n" + "-"*40)
                print(f"⏱️  Raw Position:      {offset:.2f}s")
                print(f"⌛ Latency Adjustment: +{latency:.2f}s")
                print(f"✅ Real Position:     {adjusted_offset:.2f}s")
                print(f"🔴 CURRENT:  {current_text}")
                print(f"⚪ NEXT:     {next_text}")
                print("-"*40)
            
            await asyncio.sleep(INTERVAL)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️ Loop error: {e}")
            await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(run_full_stack_poc())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")
