"""NetEase Provider (music.163.com) for synchronized lyrics"""

import requests as req
import logging
from .base import LyricsProvider
from config import get_provider_config
from logging_config import get_logger

logger = get_logger(__name__)
# logger = logging.getLogger(__name__)

class NetEaseProvider(LyricsProvider):
    def __init__(self):
        """Initialize NetEase provider with config settings"""
        super().__init__(provider_name="netease")
        
        # Get config settings
        config = get_provider_config("netease")
        
        self.headers = {
            "cookie": config.get("cookie", "NMTID=00OAVK3xqDG726ITU6jopU6jF2yMk0AAAGCO8l1BA; JSESSIONID-WYYY=8KQo11YK2GZP45RMlz8Kn80vHZ9%2FGvwzRKQXXy0iQoFKycWdBlQjbfT0MJrFa6hwRfmpfBYKeHliUPH287JC3hNW99WQjrh9b9RmKT%2Fg1Exc2VwHZcsqi7ITxQgfEiee50po28x5xTTZXKoP%2FRMctN2jpDeg57kdZrXz%2FD%2FWghb%5C4DuZ%3A1659124633932; _iuqxldmzr_=32; _ntes_nnid=0db6667097883aa9596ecfe7f188c3ec,1659122833973; _ntes_nuid=0db6667097883aa9596ecfe7f188c3ec; WNMCID=xygast.1659122837568.01.0; WEVNSM=1.0.0; WM_NI=CwbjWAFbcIzPX3dsLP%2F52VB%2Bxr572gmqAYwvN9KU5X5f1nRzBYl0SNf%2BV9FTmmYZy%2FoJLADaZS0Q8TrKfNSBNOt0HLB8rRJh9DsvMOT7%2BCGCQLbvlWAcJBJeXb1P8yZ3RHA%3D; WM_NIKE=9ca17ae2e6ffcda170e2e6ee90c65b85ae87b9aa5483ef8ab3d14a939e9a83c459959caeadce47e991fbaee82af0fea7c3b92a81a9ae8bd64b86beadaaf95c9cedac94cf5cedebfeb7c121bcaefbd8b16dafaf8fbaf67e8ee785b6b854f7baff8fd1728287a4d1d246a6f59adac560afb397bbfc25ad9684a2c76b9a8d00b2bb60b295aaafd24a8e91bcd1cb4882e8beb3c964fb9cbd97d04598e9e5a4c6499394ae97ef5d83bd86a3c96f9cbeffb1bb739aed9ea9c437e2a3; WM_TID=AAkRFnl03RdABEBEQFOBWHCPOeMra4IL; playerid=94262567")  # Get cookie from config
        }
    
    def get_lyrics(self, artist: str, title: str):
        try:
            # Search for song
            search_term = f"{artist} {title}"
            search_response = req.get(
                "https://music.163.com/api/search/pc",
                params={"s": search_term, "limit": 10, "type": 1},
                headers=self.headers
            ).json()
            
            songs = search_response.get("result", {}).get("songs")
            if not songs:
                logger.info(f"NetEase - No search results found for: {search_term}")
                return None
            
            # Get lyrics
            track_id = songs[0]["id"]
            lyrics_response = req.get(
                "https://music.163.com/api/song/lyric",
                params={"id": track_id, "lv": 1},
                headers=self.headers
            ).json()
            
            lyrics_text = lyrics_response.get("lrc", {}).get("lyric")
            if not lyrics_text:
                logger.info(f"NetEase - No lyrics found for: {search_term}")
                return None
            
            # Process lyrics
            processed_lyrics = []
            for line in lyrics_text.split("\n"):
                if not line.startswith("[") or "]" not in line: continue
                time = line[1:line.find("]")]
                m, s = time.split(":")
                seconds = float(m) * 60 + float(s)
                text = line[line.find("]") + 1:].strip()
                if text:
                    processed_lyrics.append((seconds, text))
            
            return processed_lyrics if processed_lyrics else None
            
        except Exception as e:
            logger.error(f"NetEase - Error fetching lyrics from NetEase for {search_term}: {str(e)}")
            return None 