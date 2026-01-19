#!/usr/bin/env python3
"""
Audalign Proof of Concept - Proper Recognition Test

This script tests whether audalign can:
1. Fingerprint songs (after downsampling to 16kHz mono)
2. Recognize 5-second clips extracted from those songs
3. Return the correct song name and offset position

Run with: python scripts/test_audalign_poc.py

Requirements:
- audalign (already installed)
- pydub (for audio processing)
- ffmpeg (for audio conversion)
"""

import sys
import tempfile
import shutil
from pathlib import Path

# Configuration - EDIT THESE IF NEEDED
SOURCE_DIR = Path(r"E:\Anshul\Music\New Music\ERRA\ERRA")
TEMP_DIR = Path("local_fingerprint_database/temp_test")
NUM_SONGS = 3  # Number of songs to test with
CLIP_DURATION_SEC = 5  # Duration of test clips
CLIP_POSITIONS = [5, 30, 60, 120]  # Positions to extract clips from (seconds)
TARGET_SAMPLE_RATE = 16000  # Downsample to 16kHz to reduce memory usage

# ============================================================================
# STEP 0: Setup and Imports
# ============================================================================

print("=" * 70)
print("AUDALIGN Proof of Concept - Recognition Test")
print("=" * 70)
print()

try:
    import audalign
    from pydub import AudioSegment
    print("✓ Imports successful")
except ImportError as e:
    print(f"✗ Import error: {e}")
    print("  Install with: pip install audalign pydub")
    sys.exit(1)

# Create temp directory
if TEMP_DIR.exists():
    shutil.rmtree(TEMP_DIR)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
print(f"✓ Created temp directory: {TEMP_DIR}")

# ============================================================================
# STEP 1: Find and List Source Files
# ============================================================================

print()
print("-" * 70)
print("STEP 1: Finding source files")
print("-" * 70)

flac_files = sorted(SOURCE_DIR.glob("*.flac"))[:NUM_SONGS]

if len(flac_files) < NUM_SONGS:
    print(f"✗ Need at least {NUM_SONGS} FLAC files in {SOURCE_DIR}")
    sys.exit(1)

print(f"Found {len(flac_files)} files to test:")
for i, f in enumerate(flac_files, 1):
    print(f"  {i}. {f.name}")

# ============================================================================
# STEP 2: Downsample Files to 16kHz Mono WAV
# ============================================================================

print()
print("-" * 70)
print("STEP 2: Downsampling to 16kHz mono WAV (reduces memory ~10x)")
print("-" * 70)

downsampled_files = []

for i, src_file in enumerate(flac_files, 1):
    dst_name = f"song_{i:02d}.wav"
    dst_path = TEMP_DIR / dst_name
    
    print(f"  [{i}/{len(flac_files)}] {src_file.name}")
    print(f"       -> {dst_name} ...", end=" ", flush=True)
    
    try:
        # Load and downsample
        audio = AudioSegment.from_file(str(src_file))
        audio = audio.set_frame_rate(TARGET_SAMPLE_RATE)
        audio = audio.set_channels(1)  # Mono
        
        # Export
        audio.export(str(dst_path), format="wav")
        
        downsampled_files.append({
            "original_name": src_file.name,
            "downsampled_path": dst_path,
            "duration_sec": len(audio) / 1000
        })
        
        print(f"OK ({len(audio)/1000:.1f}s)")
        
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

print()
print(f"✓ Downsampled {len(downsampled_files)} files")

# ============================================================================
# STEP 3: Fingerprint Downsampled Files
# ============================================================================

print()
print("-" * 70)
print("STEP 3: Fingerprinting downsampled files")
print("-" * 70)

rec = audalign.FingerprintRecognizer()

for i, song_info in enumerate(downsampled_files, 1):
    path = song_info["downsampled_path"]
    print(f"  [{i}/{len(downsampled_files)}] Fingerprinting {path.name}...", end=" ", flush=True)
    
    try:
        rec.fingerprint_file(str(path))
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

print()
print(f"✓ Fingerprinted {len(downsampled_files)} files")
print(f"  Files in memory: {rec.file_names}")

# ============================================================================
# STEP 4: Extract Test Clips
# ============================================================================

print()
print("-" * 70)
print("STEP 4: Extracting test clips")
print("-" * 70)

test_clips = []
clips_dir = TEMP_DIR / "clips"
clips_dir.mkdir(exist_ok=True)

for i, song_info in enumerate(downsampled_files, 1):
    song_path = song_info["downsampled_path"]
    song_name = song_info["original_name"]
    song_duration = song_info["duration_sec"]
    
    print(f"  Song {i}: {song_name} ({song_duration:.1f}s)")
    
    for pos in CLIP_POSITIONS:
        # Check if position + clip duration is within song
        if pos + CLIP_DURATION_SEC > song_duration:
            print(f"       Skipping clip at {pos}s (beyond song end)")
            continue
        
        # Load the downsampled file and extract clip
        audio = AudioSegment.from_file(str(song_path))
        start_ms = pos * 1000
        end_ms = start_ms + (CLIP_DURATION_SEC * 1000)
        clip = audio[start_ms:end_ms]
        
        # Save clip
        clip_name = f"clip_song{i}_at{pos}s.wav"
        clip_path = clips_dir / clip_name
        clip.export(str(clip_path), format="wav")
        
        test_clips.append({
            "clip_path": clip_path,
            "expected_song": song_info["downsampled_path"].name,
            "expected_offset": pos,
            "original_song": song_name,
        })
        
        print(f"       Created clip at {pos}s -> {clip_name}")

print()
print(f"✓ Created {len(test_clips)} test clips")

# ============================================================================
# STEP 5: Run Recognition Tests
# ============================================================================

print()
print("-" * 70)
print("STEP 5: Running recognition tests")
print("-" * 70)
print()

results = []

for i, clip_info in enumerate(test_clips, 1):
    clip_path = clip_info["clip_path"]
    expected_song = clip_info["expected_song"]
    expected_offset = clip_info["expected_offset"]
    
    print(f"Test {i}/{len(test_clips)}:")
    print(f"  Clip: {clip_path.name}")
    print(f"  Expected: {expected_song} @ {expected_offset}s")
    
    try:
        # Run recognition using align_files between clip and each fingerprinted file
        # This is the key test - we give it a clip and see if it matches
        
        best_match = None
        best_confidence = 0
        all_matches = []
        
        for song_info in downsampled_files:
            song_path = song_info["downsampled_path"]
            
            # Use align_files to compare clip against song
            result = audalign.align_files(
                str(clip_path),
                str(song_path),
                recognizer=rec
            )
            
            if result and "match_info" in result:
                match_info = result.get("match_info", {})
                rankings = result.get("rankings", {}).get("match_info", {})
                
                for matched_file, info in match_info.items():
                    offset_list = info.get("offset_seconds", [])
                    confidence_list = info.get("confidence", [])
                    ranking = rankings.get(matched_file, 0)
                    
                    if offset_list:
                        offset = offset_list[0] if isinstance(offset_list, list) else offset_list
                        confidence = confidence_list[0] if confidence_list else 0
                        
                        all_matches.append({
                            "song": song_path.name,
                            "offset": abs(offset),  # Offset can be negative
                            "confidence": confidence,
                            "ranking": ranking,
                        })
                        
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_match = {
                                "song": song_path.name,
                                "offset": abs(offset),
                                "confidence": confidence,
                                "ranking": ranking,
                            }
        
        if best_match:
            matched_song = best_match["song"]
            matched_offset = best_match["offset"]
            offset_error = abs(matched_offset - expected_offset)
            
            is_correct_song = matched_song == expected_song
            is_correct_offset = offset_error < 2.0  # Within 2 seconds
            
            status = "✓ PASS" if (is_correct_song and is_correct_offset) else "✗ FAIL"
            
            print(f"  Result: {status}")
            print(f"    Matched: {matched_song}")
            print(f"    Offset:  {matched_offset:.2f}s (expected {expected_offset}s, error {offset_error:.2f}s)")
            print(f"    Confidence: {best_match['confidence']}, Ranking: {best_match['ranking']}")
            
            results.append({
                "clip": clip_path.name,
                "expected_song": expected_song,
                "expected_offset": expected_offset,
                "matched_song": matched_song,
                "matched_offset": matched_offset,
                "correct_song": is_correct_song,
                "correct_offset": is_correct_offset,
                "confidence": best_match["confidence"],
                "ranking": best_match["ranking"],
            })
        else:
            print(f"  Result: ✗ NO MATCH FOUND")
            results.append({
                "clip": clip_path.name,
                "expected_song": expected_song,
                "expected_offset": expected_offset,
                "matched_song": None,
                "matched_offset": None,
                "correct_song": False,
                "correct_offset": False,
                "confidence": 0,
                "ranking": 0,
            })
        
    except Exception as e:
        print(f"  Result: ✗ ERROR: {e}")
        results.append({
            "clip": clip_path.name,
            "expected_song": expected_song,
            "expected_offset": expected_offset,
            "matched_song": None,
            "matched_offset": None,
            "correct_song": False,
            "correct_offset": False,
            "confidence": 0,
            "ranking": 0,
            "error": str(e),
        })
    
    print()

# ============================================================================
# STEP 6: Summary
# ============================================================================

print("=" * 70)
print("SUMMARY")
print("=" * 70)

total = len(results)
correct_songs = sum(1 for r in results if r["correct_song"])
correct_offsets = sum(1 for r in results if r["correct_offset"])
full_success = sum(1 for r in results if r["correct_song"] and r["correct_offset"])

print(f"Total tests:     {total}")
print(f"Correct song:    {correct_songs}/{total} ({100*correct_songs/total:.1f}%)")
print(f"Correct offset:  {correct_offsets}/{total} ({100*correct_offsets/total:.1f}%)")
print(f"Full success:    {full_success}/{total} ({100*full_success/total:.1f}%)")
print()

if full_success == total:
    print("🎉 ALL TESTS PASSED! audalign works for our use case!")
elif full_success > 0:
    print("⚠️  PARTIAL SUCCESS - audalign works but not perfectly")
else:
    print("❌ ALL TESTS FAILED - audalign may not work for our use case")

print()
print(f"Temp files in: {TEMP_DIR}")
print("(You can delete this folder when done)")
