"""Musixmatch Provider for synchronized lyrics - Uses Desktop API"""

import sys
import time
import json
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import requests

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from .base import LyricsProvider
from logging_config import get_logger

logger = get_logger(__name__)

# Global rate limiting - shared across all instances/concurrent calls
_last_request_time: float = 0


class MusixmatchProvider(LyricsProvider):
    """
    Provider for fetching synced lyrics from Musixmatch Desktop API.
    
    Uses the desktop app API (apic-desktop.musixmatch.com) which provides
    actual synced lyrics, unlike the public API which blocks richsync access.
    
    Key implementation notes:
    - Uses macro.subtitles.get endpoint for single-request lyrics fetch
    - Token-based authentication with automatic refresh
    - 10-second rate limiting to avoid captcha blocks
    """
    
    # Desktop API endpoints (not the public API which blocks synced lyrics)
    BASE_URL = "https://apic-desktop.musixmatch.com/ws/1.1/"
    APP_ID = "web-desktop-app-v1.0"
    
    # Default fallback token (from MxLRC project - https://github.com/fashni/MxLRC)
    # This is a community API token that provides Plus-tier access.
    # It's used as a fallback when dynamic token fetch fails.
    # The token is public knowledge (open source) and widely used by:
    # - MxLRC, YouTube Music plugins, Pear Desktop, etc.
    # Safe to keep: Yes - it's not a personal/private key, just a rate-limited community token.
    # If this token gets blocked, dynamic token fetch will still work.
    DEFAULT_TOKEN = "2203269256ff7abcb649269df00e14c833dbf4ddfb5b36a1aae8b0"
    
    # Rate limiting: Musixmatch can captcha-block rapid requests
    # 12 seconds is conservative but prevents rate limiting issues
    MIN_REQUEST_INTERVAL = 12.0
    
    def __init__(self):
        """Initialize Musixmatch provider with config settings"""
        super().__init__(provider_name="musixmatch")
        
        # Token management
        self._token: Optional[str] = None
        self._token_expires: float = 0
        self._token_refresh_attempts: int = 0
        
        # Rate limiting
        self._last_request_time: float = 0
        
        # Headers for desktop API
        self._headers = {
            "authority": "apic-desktop.musixmatch.com",
            "cookie": "x-mxm-token-guid=",
        }
    
    def _get_token(self) -> Optional[str]:
        """
        Get a valid token for API requests.
        Fetches new token if expired or not available.
        Falls back to DEFAULT_TOKEN if fetch fails.
        """
        # Return cached token if still valid (with 60s buffer)
        if self._token and time.time() < (self._token_expires - 60):
            return self._token
        
        # Limit refresh attempts to avoid infinite loops (for recursive get_lyrics calls)
        if self._token_refresh_attempts >= 3:
            logger.warning("Musixmatch - Too many token refresh attempts, using default")
            self._token_refresh_attempts = 0
            return self.DEFAULT_TOKEN
        
        self._token_refresh_attempts += 1
        MAX_TOKEN_RETRIES = 2  # 2 attempts for network-level retry
        
        for attempt in range(MAX_TOKEN_RETRIES):
            try:
                resp = requests.get(
                    f"{self.BASE_URL}token.get",
                    params={"app_id": self.APP_ID},
                    headers=self._headers,
                    timeout=self.timeout
                )
                
                # Handle transient HTTP errors with retry
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < MAX_TOKEN_RETRIES - 1:
                        logger.warning(f"Musixmatch - Token request got {resp.status_code}, retrying...")
                        time.sleep(2)
                        continue
                    else:
                        logger.warning(f"Musixmatch - Token request failed ({resp.status_code}), using default")
                        return self.DEFAULT_TOKEN
                
                # Process successful response
                if resp.status_code == 200:
                    # Preserve cookies from response for future requests
                    if 'set-cookie' in resp.headers:
                        cookie = resp.headers.get('set-cookie', '')
                        # Extract relevant cookie parts
                        for part in cookie.split(';'):
                            part = part.strip()
                            if part.startswith('x-mxm-'):
                                self._headers['cookie'] = part
                                break
                    
                    data = resp.json()
                    status = data.get("message", {}).get("header", {}).get("status_code")
                    
                    if status == 200:
                        token = data.get("message", {}).get("body", {}).get("user_token")
                        if token:
                            self._token = token
                            self._token_expires = time.time() + 600  # 10 minutes
                            self._token_refresh_attempts = 0
                            logger.debug(f"Musixmatch - Got new token: {token[:20]}...")
                            return self._token
                
                # Non-retryable failure (e.g., 4xx other than 429, or API-level error)
                logger.warning("Musixmatch - Token request failed, using default")
                return self.DEFAULT_TOKEN
                
            except (requests.exceptions.SSLError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if attempt < MAX_TOKEN_RETRIES - 1:
                    logger.warning(f"Musixmatch - Token request failed ({type(e).__name__}), retrying...")
                    time.sleep(2)
                    continue
                else:
                    logger.error(f"Musixmatch - Token fetch failed after retry: {e}")
                    return self.DEFAULT_TOKEN
            except Exception as e:
                logger.error(f"Musixmatch - Token fetch error: {e}")
                return self.DEFAULT_TOKEN
        
        # Shouldn't reach here, but fallback just in case
        return self.DEFAULT_TOKEN
    
    def _apply_rate_limit(self) -> None:
        """
        Apply rate limiting to avoid captcha blocks.
        Sleeps if called too soon after the last request.
        Uses module-level timestamp to enforce across ALL concurrent calls.
        """
        global _last_request_time
        time_since_last = time.time() - _last_request_time
        if time_since_last < self.MIN_REQUEST_INTERVAL:
            sleep_time = self.MIN_REQUEST_INTERVAL - time_since_last
            logger.debug(f"Musixmatch - Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        _last_request_time = time.time()
    
    def _make_request(self, url: str, params: dict) -> Optional[dict]:
        """
        Make HTTP request with retry logic, returns parsed JSON or None.
        
        Retries on:
        - SSL errors, connection errors, timeouts (exceptions)
        - 429 Too Many Requests (rate limiting)
        - 5xx Server Errors
        
        Uses self.retries (default: 3) and self.timeout (default: 10) from base class.
        
        Returns:
            Parsed JSON dict on success, None on failure after all retries.
        """
        MAX_RETRY_WAIT = 10  # Never wait more than 10 seconds
        
        for attempt in range(self.retries):
            try:
                resp = requests.get(url, params=params, headers=self._headers, timeout=self.timeout)
                
                # Handle HTTP-level rate limiting (429)
                if resp.status_code == 429:
                    if attempt < self.retries - 1:
                        retry_after = min(int(resp.headers.get('Retry-After', 2)), MAX_RETRY_WAIT)
                        logger.warning(f"Musixmatch - Rate limited (429), retry {attempt + 1}/{self.retries} in {retry_after}s")
                        time.sleep(retry_after)
                        continue
                    else:
                        logger.error(f"Musixmatch - Rate limited after {self.retries} attempts")
                        return None
                
                # Handle HTTP-level server errors (5xx)
                if resp.status_code >= 500:
                    if attempt < self.retries - 1:
                        backoff = min(2 ** attempt, MAX_RETRY_WAIT)
                        logger.warning(f"Musixmatch - Server error ({resp.status_code}), retry {attempt + 1}/{self.retries} in {backoff}s")
                        time.sleep(backoff)
                        continue
                    else:
                        logger.error(f"Musixmatch - Server error ({resp.status_code}) after {self.retries} attempts")
                        return None
                
                # For other responses, return parsed JSON (or None for non-200)
                if resp.status_code != 200:
                    logger.warning(f"Musixmatch - HTTP {resp.status_code}")
                    return None
                    
                return resp.json()
                
            except (requests.exceptions.SSLError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if attempt < self.retries - 1:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"Musixmatch - Request failed ({type(e).__name__}), retry {attempt + 1}/{self.retries} in {backoff}s")
                    time.sleep(backoff)
                else:
                    logger.error(f"Musixmatch - Request failed after {self.retries} attempts: {e}")
                    return None
            except ValueError as e:  # JSON decode error
                logger.error(f"Musixmatch - Invalid JSON response: {e}")
                return None
        return None
    
    def get_lyrics(self, artist: str, title: str, album: str = None, 
                   duration: int = None, _retry: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get lyrics using Musixmatch Desktop API.
        
        Uses macro.subtitles.get endpoint which returns:
        - Track info (matcher.track.get)
        - Plain lyrics (track.lyrics.get)  
        - Synced subtitles (track.subtitles.get) ← The important one!
        
        Args:
            artist (str): Artist name
            title (str): Track title
            album (str): Album name (optional)
            duration (int): Track duration in seconds (optional)
            _retry (bool): Internal flag to prevent infinite retry loops
            
        Returns:
            Optional[Dict[str, Any]]: Dictionary with synced lyrics and metadata or None
        """
        try:
            # Apply rate limiting
            self._apply_rate_limit()
            
            # Get valid token
            token = self._get_token()
            if not token:
                logger.warning("Musixmatch - No token available")
                return None
            
            # Clean up input strings
            artist = artist.strip()
            title = title.strip()
            
            # Build request params
            params = {
                "format": "json",
                "namespace": "lyrics_richsynched",
                "subtitle_format": "mxm",
                "app_id": self.APP_ID,
                "usertoken": token,
                "q_artist": artist,
                "q_track": title,
            }
            
            if album:
                params["q_album"] = album.strip()
            if duration:
                params["q_duration"] = str(duration)
                params["f_subtitle_length"] = str(duration)
            
            logger.info(f"Musixmatch - Searching: {artist} - {title}")
            
            # Make request to macro.subtitles.get (with network-level retry)
            data = self._make_request(f"{self.BASE_URL}macro.subtitles.get", params)
            
            # Update rate limit timestamp
            self._last_request_time = time.time()
            
            if not data:
                # _make_request already logged the error
                return None
            
            # Check response status
            header = data.get("message", {}).get("header", {})
            if header.get("status_code") != 200:
                hint = header.get("hint", "")
                if hint == "renew" and _retry:
                    # Token expired - refresh and retry once
                    logger.info("Musixmatch - Token expired, refreshing...")
                    self._token = None
                    self._token_expires = 0
                    return self.get_lyrics(artist, title, album, duration, _retry=False)
                elif hint == "captcha":
                    logger.warning("Musixmatch - Captcha required (rate limited)")
                    return None
                else:
                    logger.info(f"Musixmatch - API error: {hint or header.get('status_code')}")
                    return None
            
            # Parse macro_calls response
            body = data.get("message", {}).get("body", {})
            macro_calls = body.get("macro_calls", {})
            
            if not macro_calls:
                logger.info("Musixmatch - No macro_calls in response")
                return None
            
            # Check track match
            track_result = macro_calls.get("matcher.track.get", {}).get("message", {})
            track_status = track_result.get("header", {}).get("status_code")
            
            if track_status == 404:
                logger.info(f"Musixmatch - Track not found: {artist} - {title}")
                return None
            elif track_status == 401 and _retry:
                # Token invalid - refresh and retry once
                logger.warning("Musixmatch - Token invalid, refreshing...")
                self._token = None
                self._token_expires = 0
                return self.get_lyrics(artist, title, album, duration, _retry=False)
            elif track_status >= 500 and _retry:
                # API-level server error - retry once
                logger.warning(f"Musixmatch - API server error ({track_status}), retrying...")
                time.sleep(2)
                return self.get_lyrics(artist, title, album, duration, _retry=False)
            elif track_status != 200:
                logger.info(f"Musixmatch - Track match failed: status {track_status}")
                return None
            
            # Get track info
            track_body = track_result.get("body", {})
            track = track_body.get("track", {})
            
            if not track:
                logger.info("Musixmatch - No track data")
                return None
            
            track_name = track.get("track_name", "")
            artist_name = track.get("artist_name", "")
            is_instrumental = track.get("instrumental", 0) == 1
            has_subtitles = track.get("has_subtitles", 0) == 1
            has_richsync = track.get("has_richsync", 0) == 1
            track_id = track.get("track_id")
            commontrack_id = track.get("commontrack_id")
            
            logger.info(f"Musixmatch - Found: {track_name} by {artist_name} (subtitles: {has_subtitles}, richsync: {has_richsync})")
            
            # Handle instrumental tracks
            if is_instrumental:
                logger.info("Musixmatch - Instrumental track")
                return {
                    "lyrics": [(0.0, "Instrumental")],
                    "is_instrumental": True
                }
            
            # Get synced subtitles (line-synced)
            subtitle_result = macro_calls.get("track.subtitles.get", {}).get("message", {})
            subtitle_body = subtitle_result.get("body", {})
            
            # Handle empty body (can be list [] on 404)
            if isinstance(subtitle_body, list) or not subtitle_body:
                subtitle_body = {}
            
            subtitle_list = subtitle_body.get("subtitle_list", [])
            line_synced_lyrics = None
            
            if subtitle_list:
                subtitle = subtitle_list[0].get("subtitle", {})
                subtitle_raw = subtitle.get("subtitle_body", "")
                
                if subtitle_raw:
                    line_synced_lyrics = self._parse_subtitles(subtitle_raw)
                    if line_synced_lyrics:
                        logger.info(f"Musixmatch - Got {len(line_synced_lyrics)} synced lines")
            
            # Fetch word-synced RichSync data if available
            word_synced_lyrics = None
            if has_richsync and track_id and commontrack_id:
                word_synced_lyrics = self._fetch_richsync(track_id, commontrack_id, token)
                if word_synced_lyrics:
                    logger.info(f"Musixmatch - Got {len(word_synced_lyrics)} word-synced lines")
            
            # Return results if we have any lyrics
            if line_synced_lyrics:
                result = {
                    "lyrics": line_synced_lyrics,
                    "is_instrumental": False
                }
                # Include word-synced data if available
                if word_synced_lyrics:
                    result["word_synced_lyrics"] = word_synced_lyrics
                return result
            
            # Fallback: log if plain lyrics exist but no sync
            lyrics_result = macro_calls.get("track.lyrics.get", {}).get("message", {})
            lyrics_body = lyrics_result.get("body", {})
            
            if isinstance(lyrics_body, dict) and lyrics_body.get("lyrics"):
                lyrics_text = lyrics_body["lyrics"].get("lyrics_body", "")
                if lyrics_text:
                    logger.info("Musixmatch - Found plain lyrics only (no sync), skipping")
            
            logger.info("Musixmatch - No synced lyrics available")
            return None
            
        except requests.exceptions.Timeout:
            logger.warning("Musixmatch - Request timeout")
            return None
        except Exception as e:
            logger.error(f"Musixmatch - Error: {e}")
            return None
    
    def _fetch_richsync(self, track_id: int, commontrack_id: int, token: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch RichSync (word-synced) lyrics for a track.
        
        RichSync provides word-by-word timing data for karaoke-style display.
        
        Args:
            track_id: Musixmatch track ID
            commontrack_id: Musixmatch common track ID
            token: API token
            
        Returns:
            List of word-synced line data or None
        """
        try:
            # Note: We don't apply rate limiting here since we just made a request
            # and this is a follow-up for the same song
            # Use _make_request for retry logic on network errors
            data = self._make_request(
                f"{self.BASE_URL}track.richsync.get",
                {
                    "app_id": self.APP_ID,
                    "usertoken": token,
                    "track_id": track_id,
                    "commontrack_id": commontrack_id,
                }
            )
            
            if not data:
                logger.debug("Musixmatch - RichSync request failed")
                return None
            
            header = data.get("message", {}).get("header", {})
            
            if header.get("status_code") != 200:
                logger.debug(f"Musixmatch - RichSync not available: {header.get('hint', '')}")
                return None
            
            body = data.get("message", {}).get("body", {})
            richsync = body.get("richsync", {})
            richsync_body = richsync.get("richsync_body", "")
            
            if richsync_body:
                return self._parse_richsync(richsync_body)
            
            return None
            
        except Exception as e:
            logger.debug(f"Musixmatch - RichSync fetch error: {e}")
            return None
    
    def _parse_richsync(self, richsync_body: str) -> Optional[List[Dict[str, Any]]]:
        """
        Parse RichSync JSON format into structured word-synced data.
        
        Input format:
        [
            {
                "ts": 15.68,  // line start time (seconds)
                "te": 18.56,  // line end time (seconds)
                "l": [
                    {"c": "We", "o": 0},     // character/word, offset from ts
                    {"c": " ", "o": 0.115},
                    {"c": "were", "o": 0.213},
                    ...
                ],
                "x": "We were both young..."  // full line text
            },
            ...
        ]
        
        Output format:
        [
            {
                "start": 15.68,
                "end": 18.56,
                "text": "We were both young...",
                "words": [
                    {"word": "We", "time": 0, "duration": 0.213},
                    {"word": "were", "time": 0.213, "duration": 0.15},
                    ...  // Spaces filtered out, duration calculated from next word offset
                ]
            },
            ...
        ]
        """
        try:
            lines = json.loads(richsync_body)
            
            if not isinstance(lines, list):
                return None
            
            result = []
            
            for line in lines:
                if not isinstance(line, dict):
                    continue
                
                ts = line.get("ts", 0)  # Line start time
                te = line.get("te", 0)  # Line end time
                full_text = line.get("x", "")  # Full line text
                chars = line.get("l", [])  # Character/word list
                line_duration = te - ts if te > ts else 0
                
                # Build words list with CORRECT durations
                # Key insight: Space offsets tell us when the PREVIOUS word ends
                # So we need to track the next item's offset (including spaces) for duration
                words = []
                
                for i, char_data in enumerate(chars):
                    char = char_data.get("c", "")
                    offset = char_data.get("o", 0)
                    
                    # Skip spaces - we only want actual words
                    if not char.strip():
                        continue
                    
                    # Find the NEXT item's offset (could be space or word)
                    # This tells us when the current word ENDS
                    if i + 1 < len(chars):
                        next_offset = chars[i + 1].get("o", offset)
                        duration = next_offset - offset
                    else:
                        # Last item: duration = line end - word start
                        duration = line_duration - offset
                    
                    # Ensure duration is positive (defensive)
                    if duration < 0:
                        duration = 0.15  # Fallback to 150ms
                    
                    words.append({
                        "word": char,
                        "time": offset,  # Offset from line start
                        "duration": round(duration, 3)
                    })
                
                if words or full_text:
                    result.append({
                        "start": ts,
                        "end": te,
                        "text": full_text,
                        "words": words
                    })
            
            return result if result else None
            
        except json.JSONDecodeError as e:
            logger.debug(f"Musixmatch - RichSync JSON parse error: {e}")
            return None
        except Exception as e:
            logger.debug(f"Musixmatch - RichSync parse error: {e}")
            return None
    
    def _parse_subtitles(self, subtitle_body: str) -> Optional[List[Tuple[float, str]]]:
        """
        Parse synced subtitles from Musixmatch JSON format.
        
        Format: [{"text": "lyrics", "time": {"total": 1.5, "minutes": 0, "seconds": 1, "hundredths": 50}}, ...]
        
        Args:
            subtitle_body: JSON string containing subtitle data
            
        Returns:
            List of (timestamp, text) tuples or None
        """
        try:
            lines = json.loads(subtitle_body)
            
            if not isinstance(lines, list):
                return None
            
            result = []
            
            for line in lines:
                if not isinstance(line, dict):
                    continue
                
                text = line.get("text", "").strip()
                time_data = line.get("time", {})
                
                # Use 'total' if available, otherwise calculate from components
                if "total" in time_data:
                    total_seconds = float(time_data["total"])
                else:
                    minutes = time_data.get("minutes", 0)
                    seconds = time_data.get("seconds", 0)
                    hundredths = time_data.get("hundredths", 0)
                    total_seconds = minutes * 60 + seconds + hundredths / 100
                
                # Include empty lines as instrumental breaks (♪)
                if not text:
                    text = "♪"
                
                result.append((total_seconds, text))
            
            return result if result else None
            
        except json.JSONDecodeError as e:
            logger.error(f"Musixmatch - JSON parse error: {e}")
            return None
        except Exception as e:
            logger.error(f"Musixmatch - Parse error: {e}")
            return None
