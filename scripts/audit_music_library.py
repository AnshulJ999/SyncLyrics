#!/usr/bin/env python3
"""
Music Library Audit Script

Scans a music library and reports on metadata quality.
This is a prerequisite for Dejavu fingerprinting - we need to ensure
metadata (artist, title) is accurate before indexing.

Usage:
    python scripts/audit_music_library.py "E:/Anshul/Music/New Music"
    python scripts/audit_music_library.py "E:/Anshul/Music/New Music" --output report.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("WARNING: mutagen not installed. Install with: pip install mutagen")


def extract_metadata(filepath: Path) -> dict:
    """
    Extract metadata from an audio file.
    
    Returns dict with:
        - artist: str or None
        - title: str or None  
        - album: str or None
        - duration: float (seconds)
        - has_tags: bool
        - tag_source: 'id3' | 'vorbis' | 'filename' | 'none'
        - issues: list of issues found
    """
    result = {
        'filepath': str(filepath),
        'filename': filepath.name,
        'extension': filepath.suffix.lower(),
        'artist': None,
        'title': None,
        'album': None,
        'duration': None,
        'has_tags': False,
        'tag_source': 'none',
        'issues': []
    }
    
    if not MUTAGEN_AVAILABLE:
        result['issues'].append('mutagen not installed')
        return result
    
    try:
        if filepath.suffix.lower() == '.flac':
            audio = FLAC(filepath)
            result['duration'] = audio.info.length
            
            # FLAC uses Vorbis comments
            artist = audio.get('artist', [None])[0]
            title = audio.get('title', [None])[0]
            album = audio.get('album', [None])[0]
            
            if artist or title:
                result['tag_source'] = 'vorbis'
                result['has_tags'] = True
            
            result['artist'] = artist
            result['title'] = title
            result['album'] = album
            
        elif filepath.suffix.lower() == '.mp3':
            audio = MP3(filepath)
            result['duration'] = audio.info.length
            
            # MP3 uses ID3 tags
            if audio.tags:
                result['tag_source'] = 'id3'
                result['has_tags'] = True
                
                # ID3 tag names
                result['artist'] = str(audio.tags.get('TPE1', [''])[0]) or None
                result['title'] = str(audio.tags.get('TIT2', [''])[0]) or None
                result['album'] = str(audio.tags.get('TALB', [''])[0]) or None
        
        # Check for issues
        if not result['artist']:
            result['issues'].append('missing_artist')
        if not result['title']:
            result['issues'].append('missing_title')
        if result['artist'] and result['artist'].lower() in ['unknown', 'unknown artist', 'various artists']:
            result['issues'].append('generic_artist')
        if result['title'] and result['title'].lower().startswith('track'):
            result['issues'].append('generic_title')
            
        # Try to parse from filename if tags missing
        if not result['artist'] or not result['title']:
            parsed = parse_filename(filepath.stem)
            if parsed['artist'] and not result['artist']:
                result['artist'] = parsed['artist']
                result['issues'].append('artist_from_filename')
            if parsed['title'] and not result['title']:
                result['title'] = parsed['title']
                result['issues'].append('title_from_filename')
                
    except Exception as e:
        result['issues'].append(f'read_error: {str(e)[:50]}')
    
    return result


def parse_filename(filename: str) -> dict:
    """
    Try to parse artist and title from filename.
    
    Common patterns:
        "Artist - Title"
        "01 - Title"
        "01. Title"
        "Title"
    """
    result = {'artist': None, 'title': None}
    
    # Remove common prefixes like track numbers
    import re
    # Remove leading track numbers: "01 - ", "01. ", "1 ", etc.
    cleaned = re.sub(r'^(\d{1,2}[\.\-\s]+)', '', filename).strip()
    
    # Try "Artist - Title" pattern
    if ' - ' in cleaned:
        parts = cleaned.split(' - ', 1)
        # Check if first part looks like artist (not a number)
        if parts[0] and not parts[0].isdigit():
            result['artist'] = parts[0].strip()
            result['title'] = parts[1].strip() if len(parts) > 1 else None
        else:
            result['title'] = parts[1].strip() if len(parts) > 1 else cleaned
    else:
        result['title'] = cleaned
    
    return result


def scan_library(root_path: Path, extensions: list = ['.flac', '.mp3']) -> dict:
    """
    Scan music library and collect metadata for all audio files.
    
    Returns comprehensive report dict.
    """
    print(f"\n🔍 Scanning: {root_path}")
    print(f"   Extensions: {extensions}")
    print()
    
    files = []
    for ext in extensions:
        files.extend(root_path.rglob(f'*{ext}'))
    
    total = len(files)
    print(f"📁 Found {total} audio files")
    print()
    
    results = []
    issues_count = defaultdict(int)
    
    for i, filepath in enumerate(files):
        # Progress indicator
        if (i + 1) % 100 == 0 or i == 0:
            print(f"   Processing: {i + 1}/{total} ({(i + 1) * 100 // total}%)")
        
        meta = extract_metadata(filepath)
        results.append(meta)
        
        for issue in meta['issues']:
            issues_count[issue] += 1
    
    print(f"\n✅ Scanned {total} files")
    
    # Build report
    report = {
        'scan_date': datetime.now().isoformat(),
        'root_path': str(root_path),
        'total_files': total,
        'by_extension': defaultdict(int),
        'issues_summary': dict(issues_count),
        'has_valid_tags': 0,
        'missing_artist': 0,
        'missing_title': 0,
        'files': results
    }
    
    for r in results:
        report['by_extension'][r['extension']] += 1
        if r['has_tags'] and r['artist'] and r['title']:
            report['has_valid_tags'] += 1
        if not r['artist']:
            report['missing_artist'] += 1
        if not r['title']:
            report['missing_title'] += 1
    
    report['by_extension'] = dict(report['by_extension'])
    
    return report


def print_report(report: dict, show_all_issues: bool = False):
    """Print a human-readable summary of the audit report."""
    
    print("\n" + "=" * 60)
    print("📊 MUSIC LIBRARY AUDIT REPORT")
    print("=" * 60)
    
    print(f"\n📁 Root: {report['root_path']}")
    print(f"📅 Scanned: {report['scan_date'][:19]}")
    
    print(f"\n📈 STATISTICS")
    print(f"   Total files: {report['total_files']}")
    for ext, count in report['by_extension'].items():
        print(f"   {ext}: {count}")
    
    valid_pct = (report['has_valid_tags'] / report['total_files'] * 100) if report['total_files'] > 0 else 0
    print(f"\n✅ METADATA QUALITY")
    print(f"   Valid tags (artist + title): {report['has_valid_tags']} ({valid_pct:.1f}%)")
    print(f"   Missing artist: {report['missing_artist']}")
    print(f"   Missing title: {report['missing_title']}")
    
    if report['issues_summary']:
        print(f"\n⚠️  ISSUES FOUND")
        for issue, count in sorted(report['issues_summary'].items(), key=lambda x: -x[1]):
            print(f"   {issue}: {count}")
    
    # Show sample problematic files
    problem_files = [f for f in report['files'] if f['issues'] and 'read_error' not in str(f['issues'])]
    if problem_files:
        print(f"\n📋 SAMPLE PROBLEMATIC FILES (showing up to 20)")
        for f in problem_files[:20]:
            issues_str = ', '.join(f['issues'])
            print(f"   [{issues_str}]")
            print(f"      {f['filepath']}")
            if f['artist'] or f['title']:
                print(f"      Tags: {f['artist']} - {f['title']}")
            print()
    
    # Show files with read errors
    error_files = [f for f in report['files'] if any('read_error' in str(i) for i in f['issues'])]
    if error_files:
        print(f"\n❌ FILES WITH READ ERRORS ({len(error_files)})")
        for f in error_files[:10]:
            print(f"   {f['filepath']}")
            print(f"      {f['issues']}")
    
    print("\n" + "=" * 60)
    
    # Final verdict
    if valid_pct >= 95:
        print("✅ VERDICT: Library is well-tagged! Ready for fingerprinting.")
    elif valid_pct >= 80:
        print("⚠️  VERDICT: Most files are tagged. Consider fixing issues before fingerprinting.")
    else:
        print("❌ VERDICT: Many files have missing tags. Recommend using MusicBrainz Picard first.")
    
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Audit music library metadata quality"
    )
    parser.add_argument(
        "directory",
        help="Path to music library root"
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".flac", ".mp3"],
        help="File extensions to scan (default: .flac .mp3)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Save full report to JSON file"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only show summary, not individual issues"
    )
    
    args = parser.parse_args()
    
    root = Path(args.directory)
    if not root.exists():
        print(f"ERROR: Directory not found: {root}")
        sys.exit(1)
    
    if not MUTAGEN_AVAILABLE:
        print("\n❌ ERROR: mutagen library is required")
        print("   Install with: pip install mutagen")
        sys.exit(1)
    
    # Run scan
    report = scan_library(root, args.extensions)
    
    # Print summary
    print_report(report, show_all_issues=not args.quiet)
    
    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"📄 Full report saved to: {output_path}")


if __name__ == "__main__":
    main()
