
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from providers.lrclib import LRCLIBProvider
from providers.netease import NetEaseProvider


def test_providers():
    # Test LRCLIB
    lrclib = LRCLIBProvider()
    lyrics = lrclib.get_lyrics("Rick Astley", "Never Gonna Give You Up")
    print("LRCLIB Results:", lyrics is not None)
    
    # Test NetEase
    netease = NetEaseProvider()
    lyrics = netease.get_lyrics("Rick Astley", "Never Gonna Give You Up")
    print("NetEase Results:", lyrics is not None)

if __name__ == "__main__":
    test_providers() 