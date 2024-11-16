"""Test LRCLIB API requests"""

import requests
import logging

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# API Constants
BASE_URL = "https://lrclib.net/api"
HEADERS = {
    "Lrclib-Client": "SyncLyrics v1.0.0 (https://github.com/AnshulJ999/SyncLyrics)"
}

def test_api():
    # Test song
    artist = "Skyharbor"
    title = "Guiding Lights"
    
    # 1. Try direct API call
    params = {
        "artist_name": artist,
        "track_name": title
    }
    
    logger.info(f"\nTesting LRCLIB API with: {artist} - {title}")
    logger.info(f"GET {BASE_URL}/get")
    logger.info(f"Params: {params}")
    
    response = requests.get(f"{BASE_URL}/get", params=params, headers=HEADERS)
    logger.info(f"Status: {response.status_code}")
    logger.info(f"Response: {response.text}\n")
    
    # 2. Try search if direct call fails
    if response.status_code == 404:
        logger.info("Trying search endpoint...")
        search_response = requests.get(
            f"{BASE_URL}/search",
            params={"q": f"{artist} {title}"},
            headers=HEADERS
        )
        logger.info(f"Status: {search_response.status_code}")
        logger.info(f"Response: {search_response.text}")

if __name__ == "__main__":
    test_api()