"""Test file for LRCLIB Provider"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

import logging
import requests
from providers.lrclib import LRCLIBProvider

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_lrclib_request():
    """Test LRCLIB API requests and responses"""
    
    # Initialize provider
    provider = LRCLIBProvider()
    
    # Test case
    test_cases = [
        {
            "artist": "Skyharbor",
            "title": "Guiding Lights",
            "album": "Guiding Lights",  # TODO: Where do we get this?
            "duration": 368  # TODO: Where do we get this?
        }
    ]
    
    for test in test_cases:
        logger.info(f"\nTesting with: {test}")
        
        # 1. First try direct API call
        params = {
            "artist_name": test["artist"],
            "track_name": test["title"]
        }
        if test.get("album"):
            params["album_name"] = test["album"]
        if test.get("duration"):
            params["duration"] = test["duration"]
            
        # Log the exact request being made
        url = f"{provider.BASE_URL}/get"
        logger.debug(f"Making request to: {url}")
        logger.debug(f"With params: {params}")
        logger.debug(f"With headers: {provider.HEADERS}")
        
        try:
            response = requests.get(url, params=params, headers=provider.HEADERS)
            logger.debug(f"Response status: {response.status_code}")
            logger.debug(f"Response content: {response.text}")
            
            if response.ok:
                data = response.json()
                logger.info("Success! Found lyrics directly")
                logger.debug(f"Returned data: {data}")
            else:
                logger.info("Direct match failed, trying search...")
                
                # 2. Try search endpoint
                search_url = f"{provider.BASE_URL}/search"
                search_params = {
                    "track_name": test["title"],
                    "artist_name": test["artist"]
                }
                
                logger.debug(f"Making search request to: {search_url}")
                logger.debug(f"With params: {search_params}")
                
                search_response = requests.get(search_url, params=search_params, headers=provider.HEADERS)
                logger.debug(f"Search response status: {search_response.status_code}")
                logger.debug(f"Search response content: {search_response.text}")
                
        except Exception as e:
            logger.error(f"Error during request: {str(e)}")

if __name__ == "__main__":
    test_lrclib_request()