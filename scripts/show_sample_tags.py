#!/usr/bin/env python3
"""
Show sample tags from audio files for verification.

Usage:
    python scripts/show_sample_tags.py "E:/Anshul/Music/New Music" --samples 20
    python scripts/show_sample_tags.py "E:/Anshul/Music/New Music" --random
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mutagen.flac import FLAC
from mutagen.mp3 import MP3


def show_file_tags(filepath: Path):
    """Display all tags for a single file."""
    print(f"\n{'='*60}")
    print(f"📄 FILE: {filepath.name}")
    print(f"   PATH: {filepath}")
    print(f"{'='*60}")
    
    try:
        if filepath.suffix.lower() == '.flac':
            audio = FLAC(filepath)
            print(f"   Duration: {audio.info.length:.1f}s ({audio.info.length/60:.1f} min)")
            print(f"   Sample Rate: {audio.info.sample_rate} Hz")
            print(f"   Bits: {audio.info.bits_per_sample}")
            print()
            print("   📋 TAGS:")
            if audio.tags:
                for key, value in sorted(audio.tags.items()):
                    # Truncate long values
                    val_str = str(value[0]) if value else ''
                    if len(val_str) > 60:
                        val_str = val_str[:60] + '...'
                    print(f"      {key}: {val_str}")
            else:
                print("      (no tags)")
                
        elif filepath.suffix.lower() == '.mp3':
            audio = MP3(filepath)
            print(f"   Duration: {audio.info.length:.1f}s ({audio.info.length/60:.1f} min)")
            print()
            print("   📋 TAGS:")
            if audio.tags:
                for key in sorted(audio.tags.keys()):
                    value = audio.tags[key]
                    val_str = str(value)
                    if len(val_str) > 60:
                        val_str = val_str[:60] + '...'
                    print(f"      {key}: {val_str}")
            else:
                print("      (no tags)")
                
    except Exception as e:
        print(f"   ❌ ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(description="Show sample audio file tags")
    parser.add_argument("directory", help="Path to music library")
    parser.add_argument("--samples", "-n", type=int, default=10, help="Number of samples to show")
    parser.add_argument("--random", "-r", action="store_true", help="Random sampling")
    parser.add_argument("--artist", "-a", help="Filter by artist folder name")
    parser.add_argument("--long", "-l", action="store_true", help="Show files >10 min (potential full albums)")
    
    args = parser.parse_args()
    
    root = Path(args.directory)
    if not root.exists():
        print(f"ERROR: Directory not found: {root}")
        sys.exit(1)
    
    # Find files
    files = list(root.rglob('*.flac')) + list(root.rglob('*.mp3'))
    
    # Filter by artist if specified
    if args.artist:
        files = [f for f in files if args.artist.lower() in str(f).lower()]
    
    # Filter long files if requested
    if args.long:
        long_files = []
        print("Scanning for long files (>10 min)...")
        for f in files:
            try:
                if f.suffix.lower() == '.flac':
                    audio = FLAC(f)
                    if audio.info.length > 600:  # 10 min
                        long_files.append(f)
                elif f.suffix.lower() == '.mp3':
                    audio = MP3(f)
                    if audio.info.length > 600:
                        long_files.append(f)
            except:
                pass
        files = long_files
        print(f"Found {len(files)} long files")
    
    if not files:
        print("No matching files found")
        sys.exit(0)
    
    # Select samples
    if args.random:
        samples = random.sample(files, min(args.samples, len(files)))
    else:
        samples = files[:args.samples]
    
    print(f"\n📊 Showing {len(samples)} of {len(files)} files")
    
    for f in samples:
        show_file_tags(f)
    
    print("\n" + "="*60)
    print("Done!")


if __name__ == "__main__":
    main()
