import requests as req

from system_utils import get_current_song_meta_data


current_song_data = None
current_song_lyrics = None


async def _update_song():
    """
    This function updates the current song data and lyrics (the global variables).
    """

    global current_song_lyrics, current_song_data

    new_song_data = await get_current_song_meta_data()

    should_fetch_lyrics = new_song_data is not None and (
        current_song_data is None or (
            current_song_data["artist"] != new_song_data["artist"] or
            current_song_data["title"] != new_song_data["title"]
        ))

    if should_fetch_lyrics:
        current_song_lyrics = _get_lyrics(new_song_data["artist"], new_song_data["title"])
            
    current_song_data = new_song_data


def _get_lyrics(artist: str, title: str) -> list[tuple[float, str]]:
    """
    This function returns the lyrics of the given song by using the lrclib.net API.

    Args:
        artist (str): The artist of the song.
        title (str): The title of the song.

    Returns:
        list[tuple[float, str]]: The lyrics of the song.
    """

    artist_title= f"{artist} {title}"
    song_id = req.request("GET", f"https://lrclib.net/api/search?q={artist_title}").json()
    if len(song_id) == 0: return None
    song_id = song_id[0]["id"]
    lyrics = req.request("GET", f"https://lrclib.net/api/get/{song_id}").json()["syncedLyrics"]
    if lyrics is None: return None

    processed_lyrics = []
    for lyric in lyrics.split("\n"):
        time = lyric[1: lyric.find("]") -1]
        m, s = time.split(":")
        seconds = float(m) * 60 + float(s)
        processed_lyrics.append((seconds, lyric[lyric.find("]") + 1:].strip()))
    return processed_lyrics


def _find_current_lyric_index(delta: float = 0.15) -> int: # latency compensation - positive=earlier, negative=later. Current value is 150 ms EARLIER. 
    """
    This function returns the index of the current lyric in the current_song_lyrics list.

    Args:
        delta (float, optional): A delay to take into account when calculating the index. Defaults to 0.1.

    Returns:
        int: The index of the current lyric in the current_song_lyrics list. If a lyric is not found, -1 is returned.
    """

    if current_song_lyrics is not None and current_song_data is not None:
        time = current_song_data["position"]
        for i in range(len(current_song_lyrics) - 1):
            if current_song_lyrics[i][0] <= time + delta < current_song_lyrics[i + 1][0]:
                return i
    return -1


async def get_timed_lyrics(delta: int = 0) -> str: # delta for latency compensation doesn't work rn
    """
    This function returns the current lyric of the song.

    Args:
        delta (int, optional): The delay to take into account when calculating the lyric. Defaults to 0.

    Returns:
        str: The current lyric of the song. If a lyric is not found, "Lyrics not found" is returned.
    """

    await _update_song()
    lyric_index = _find_current_lyric_index(delta)
    if lyric_index == -1: return "Lyrics not found"
    return current_song_lyrics[lyric_index][1]


async def get_timed_lyrics_previous_and_next() -> tuple[str, ...] | str:
    """
    This function returns multiple lines of lyrics, including previous and next lines.
    Returns:
        tuple[str, ...] | str: Multiple lines of lyrics centered around the current line,
                              or "Lyrics not found" if no lyrics are available.
    """
    def _lyric_representation(lyric_index: int) -> str:
        """Get lyric at index with bounds checking"""
        if current_song_lyrics is None:
            return "-"
        if lyric_index < 0 or lyric_index >= len(current_song_lyrics):
            return "-"
        return current_song_lyrics[lyric_index][1] or "-"

    await _update_song()
    lyric_index = _find_current_lyric_index()
    if lyric_index == -1 or current_song_lyrics is None:
        return "Lyrics not found"
    
    # Return 6 lines total: 2 previous, current, and 3 next
    return (
        _lyric_representation(lyric_index-2),
        _lyric_representation(lyric_index-1),
        _lyric_representation(lyric_index),
        _lyric_representation(lyric_index+1),
        _lyric_representation(lyric_index+2),
        _lyric_representation(lyric_index+3)
    )