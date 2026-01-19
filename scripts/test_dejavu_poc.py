#!/usr/bin/env python3
"""
Test Dejavu Indexing - Proof of Concept

Usage:
    python scripts/test_dejavu_poc.py "E:/Anshul/Music/New Music/ERRA"
    python scripts/test_dejavu_poc.py --recognize "path/to/test.wav"
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_index(directory: str):
    """Test indexing a directory."""
    from audio_recognition.dejavu import DejavuRecognizer
    
    print(f"\n🎵 Dejavu Proof of Concept - Indexing Test")
    print(f"=" * 50)
    print(f"Directory: {directory}")
    print()
    
    recognizer = DejavuRecognizer()
    
    print(f"Database path: {recognizer.db_path}")
    print(f"Current stats: {recognizer.get_stats()}")
    print()
    
    print("Starting indexing...")
    print("-" * 50)
    
    results = recognizer.index_directory(directory)
    
    print("-" * 50)
    print(f"\n✅ Indexing complete!")
    print(f"   Indexed: {results['indexed']}")
    print(f"   Skipped: {results['skipped']}")
    print(f"   Failed:  {results.get('failed', 0)}")
    print()
    
    stats = recognizer.get_stats()
    print(f"📊 Database stats:")
    print(f"   Songs: {stats['song_count']}")
    print(f"   Files: {stats['indexed_files']}")
    print(f"   Size:  {stats['db_size_mb']:.2f} MB")
    print()
    
    # Show indexed songs
    print("📋 Indexed songs:")
    for song_key in list(recognizer._metadata.keys())[:20]:
        meta = recognizer._metadata[song_key]
        print(f"   {song_key} ({meta['duration']:.0f}s)")
    
    if len(recognizer._metadata) > 20:
        print(f"   ... and {len(recognizer._metadata) - 20} more")


def test_recognize(wav_path: str):
    """Test recognition with a WAV file."""
    import asyncio
    from audio_recognition.dejavu import DejavuRecognizer
    
    print(f"\n🔍 Dejavu Recognition Test")
    print(f"=" * 50)
    print(f"WAV file: {wav_path}")
    print()
    
    recognizer = DejavuRecognizer()
    
    if not recognizer.is_available():
        print("❌ No indexed songs! Run indexing first.")
        return
    
    print(f"Database has {recognizer.get_stats()['song_count']} songs")
    print()
    
    with open(wav_path, 'rb') as f:
        wav_bytes = f.read()
    
    print("Recognizing...")
    result = asyncio.run(recognizer.recognize(wav_bytes))
    
    if result:
        print(f"\n✅ Match found!")
        best = result['best_match']
        print(f"   Song: {best['song_key']}")
        print(f"   Confidence: {best['confidence']:.2%}")
        print(f"   Offset: {best['offset']:.2f}s")
        
        if len(result['matches']) > 1:
            print(f"\n   Other matches:")
            for m in result['matches'][1:5]:
                print(f"      {m['song_key']} ({m['confidence']:.2%})")
    else:
        print(f"\n❌ No match found")


def main():
    parser = argparse.ArgumentParser(description="Test Dejavu POC")
    parser.add_argument("directory", nargs="?", help="Directory to index")
    parser.add_argument("--recognize", "-r", help="WAV file to recognize")
    parser.add_argument("--stats", "-s", action="store_true", help="Show stats only")
    
    args = parser.parse_args()
    
    if args.stats:
        from audio_recognition.dejavu import DejavuRecognizer
        recognizer = DejavuRecognizer()
        stats = recognizer.get_stats()
        print(f"\n📊 Dejavu Stats:")
        for k, v in stats.items():
            print(f"   {k}: {v}")
        return
    
    if args.recognize:
        test_recognize(args.recognize)
    elif args.directory:
        test_index(args.directory)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
