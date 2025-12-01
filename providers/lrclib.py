"""LRCLIB Provider for synchronized lyrics"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

import logging
from typing import Optional, Dict, Any

import requests as req
from .base import LyricsProvider
from config import get_provider_config
from logging_config import get_logger

logger = get_logger(__name__)

class LRCLIBProvider(LyricsProvider):
    # Define constants for the API
    BASE_URL = "https://lrclib.net/api"
    HEADERS = {
        "User-Agent": "SyncLyrics v1.0.0 (https://github.com/AnshulJ999/SyncLyrics)",
        "Lrclib-Client": "SyncLyrics v1.0.0 (https://github.com/AnshulJ999/SyncLyrics)"
    }
    
    def __init__(self):
        """Initialize LRCLIB provider with config settings"""
        super().__init__(provider_name="lrclib")
        
        # Get config settings
        config = get_provider_config("lrclib")
        
        self.BASE_URL = config.get("base_url", self.BASE_URL)
        self.HEADERS.update(config.get("headers", {}))  # Add any additional headers from config
    
    def get_lyrics(self, artist: str, title: str, album: str = None, duration: int = None) -> Optional[Dict[str, Any]]:
        """
        Get lyrics using LRCLIB API
        Args:
            artist (str): Artist name
            title (str): Track title
            album (str): Album name (optional)
            duration (int): Track duration in seconds (optional)
        """
        try:
            # Clean up input strings
            artist = artist.strip()
            title = title.strip()
            if album:
                album = album.strip()

            response = None
            
            # 1. Try /api/get ONLY if we have a duration (Required by API)
            if duration:
                params = {
                    "artist_name": artist,
                    "track_name": title,
                    "duration": duration
                }
                if album:
                    params["album_name"] = album

                logger.info(f"LRCLib - Trying exact match with params: {params}")
                
                try:
                    resp = req.get(
                        f"{self.BASE_URL}/get", 
                        params=params,
                        headers=self.HEADERS,
                        timeout=10
                    )
                    if resp.status_code == 200:
                        response = resp.json()
                    elif resp.status_code == 404:
                        logger.info("LRCLib - Exact match 404 Not Found")
                    else:
                        logger.warning(f"LRCLib - Exact match returned status {resp.status_code}")
                except Exception as e:
                    logger.error(f"LRCLib - Exact match request failed: {e}")

            # 2. Fallback to /api/search if:
            #    a) No duration provided (skipped /get)
            #    b) /get returned 404 or error
            #    c) /get returned 200 but no synced lyrics AND not instrumental
            
            has_synced = response and response.get("syncedLyrics")
            is_instrumental_from_get = response and response.get("instrumental", False)
            
            # If /get found an instrumental track (even without synced lyrics), use it
            # Don't fall back to search - we already have the answer
            if not has_synced and is_instrumental_from_get:
                logger.info(f"LRCLib - Exact match found instrumental track (no synced lyrics), using it")
                # response is already set, will be processed below
            elif not has_synced:
                reason = "No duration provided" if not duration else "No synced lyrics in exact match"
                logger.info(f"LRCLib - {reason}, trying search with specific fields")
                
                search_params = {
                    "track_name": title,
                    "artist_name": artist
                }
                if album:
                    search_params["album_name"] = album
                    
                try:
                    search_resp = req.get(
                        f"{self.BASE_URL}/search",
                        params=search_params,
                        headers=self.HEADERS,
                        timeout=10
                    )
                    
                    search_result = []
                    if search_resp.status_code == 200:
                        search_result = search_resp.json()
                    
                    # If specific search fails, try general search as last resort
                    if not search_result:
                        logger.info(f"LRCLib - No results with specific fields, trying general search")
                        gen_resp = req.get(
                            f"{self.BASE_URL}/search",
                            params={"q": f"{artist} {title}"},
                            headers=self.HEADERS,
                            timeout=10
                        )
                        if gen_resp.status_code == 200:
                            search_result = gen_resp.json()
                    
                    if not search_result: 
                        logger.info(f"LRCLib - No search results found for: {artist} - {title}")
                        return None
                    
                    # Iterate through search results to find one with synced lyrics OR instrumental flag
                    # Accept instrumental tracks even if they don't have synced lyrics
                    found_match = False
                    for result in search_result:
                        if result.get("syncedLyrics") or result.get("instrumental"):
                            response = result
                            found_match = True
                            logger.info(f"LRCLib - Found match in search results: {result.get('name')} by {result.get('artistName')} (instrumental: {result.get('instrumental', False)})")
                            break
                    
                    if not found_match:
                        logger.info(f"LRCLib - Search results found but none had synced lyrics or instrumental flag")
                        return None
                        
                except Exception as e:
                    logger.error(f"LRCLib - Search request failed: {e}")
                    return None

            # Extract synced lyrics
            if not response:
                return None

            is_instrumental = bool(response.get("instrumental"))
            plain_lyrics = response.get("plainLyrics", "")

            lyrics = response.get("syncedLyrics")
            # Allow instrumental tracks to proceed even without synced lyrics
            # They will be handled by the empty processed_lyrics check below
            if not lyrics and not is_instrumental:
                logger.info(f"LRCLib - No synced lyrics found for: {artist} - {title}")
                return None

            # Process lyrics SAFE PARSING
            # If instrumental and no lyrics, skip parsing (will be handled below)
            processed_lyrics = []
            if lyrics:  # Only parse if lyrics exist
                for line in lyrics.split("\n"):
                    try:
                        if not line.strip() or "]" not in line: continue
                        
                        # Parse Timestamp
                        time_part = line[1: line.find("]")]
                        
                        # Skip meta tags like [by:...] or [ar:...]
                        if not time_part[0].isdigit(): continue

                        m, s = time_part.split(":")
                        seconds = float(m) * 60 + float(s)
                        text = line[line.find("]") + 1:].strip()
                        
                        processed_lyrics.append((seconds, text))
                    except ValueError:
                        continue # Skip lines that fail to parse
            
            if not processed_lyrics:
                if is_instrumental:
                    processed_lyrics = [(0.0, "Instrumental")]
                else:
                    return None

            metadata = {"is_instrumental": is_instrumental}
            if plain_lyrics:
                metadata["plain_lyrics"] = plain_lyrics

            return {
                "lyrics": processed_lyrics,
                **metadata
            }
            
        except Exception as e:
            logger.error(f"LRCLib - Error fetching lyrics for {artist} - {title}: {str(e)}")
            return None