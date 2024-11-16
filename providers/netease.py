"""NetEase Provider (music.163.com) for synchronized lyrics"""

import requests as req
import logging
from .base import LyricsProvider

logger = logging.getLogger(__name__)

class NetEaseProvider(LyricsProvider):
    def __init__(self):
        super().__init__(name="NetEase", priority=2)
        self.headers = {
            "cookie": "NMTID=00OAVK3xqDG726ITU6jopU6jF2yMk0AAAGCO8l1BA",  # Shortened for brevity
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