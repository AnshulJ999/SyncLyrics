"""
Recognition Blacklist Module

Loads a user-managed blacklist.json from DATA_DIR and provides
case-insensitive glob matching to reject unwanted recognition results.

Supports:
  - artists: block all songs by a specific artist (supports * wildcards)
  - titles: block specific song titles (supports * wildcards)
  - combos: block specific artist+title combinations (supports * wildcards)

File lives at DATA_DIR/blacklist.json (next to settings.json).
The application never writes to this file after optional template creation;
it is treated as read-only after load.
"""

import fnmatch
import json
from pathlib import Path
from typing import Any, Dict, List

from logging_config import get_logger

logger = get_logger(__name__)

# Set to True to auto-create a template blacklist.json on first run.
# Currently disabled: this feature is personal-only, not for general users yet.
_CREATE_TEMPLATE_IF_MISSING = False

_TEMPLATE_CONTENT = {
    "_version": 1,
    "_updated": "edit this manually when you change the file",
    "_comment": "Case-insensitive matching. Use * for wildcards. App never writes to this file.",
    "artists": [
        "pekkon"
    ],
    "titles": [],
    "combos": [
        {"artist": "pekkon", "title": "*"}
    ]
}


class RecognitionBlacklist:
    """
    Loads and evaluates the recognition blacklist.

    Designed to be instantiated once per recognizer (__init__) and reused
    across all recognition cycles. No hot-reload: restart the app to apply
    blacklist changes.

    Evaluation order:
      1. Artists list (blocks all songs from that artist)
      2. Titles list (blocks any song with that title, regardless of artist)
      3. Combos list (blocks specific artist+title pairs)

    All matching is case-insensitive and supports fnmatch glob patterns (*).
    """

    def __init__(self, blacklist_path: Path):
        """
        Load the blacklist from disk.

        Args:
            blacklist_path: Absolute path to blacklist.json
        """
        self._path = blacklist_path
        self._artists: List[str] = []
        self._titles: List[str] = []
        self._combos: List[Dict[str, str]] = []
        self._active = False  # True only if file loaded successfully with content

        if not blacklist_path.exists():
            logger.info(f"Blacklist file not found: {blacklist_path} (no filtering active)")
            if _CREATE_TEMPLATE_IF_MISSING:
                self._create_template(blacklist_path)
            return

        try:
            with open(blacklist_path, encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)

            self._artists = [str(a) for a in data.get("artists", []) if a]
            self._titles = [str(t) for t in data.get("titles", []) if t]

            # Combos: list of {"artist": ..., "title": ...} dicts
            raw_combos = data.get("combos", [])
            for entry in raw_combos:
                if not isinstance(entry, dict):
                    continue
                a = entry.get("artist", "")
                t = entry.get("title", "")
                if a or t:  # Accept partial combos (empty side = match anything)
                    self._combos.append({"artist": str(a), "title": str(t)})

            total = len(self._artists) + len(self._titles) + len(self._combos)
            self._active = total > 0

            logger.info(
                f"Blacklist loaded: {len(self._artists)} artists, "
                f"{len(self._titles)} titles, "
                f"{len(self._combos)} combos "
                f"(total {total} rules) from {blacklist_path.name}"
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Blacklist JSON parse error: {e} — continuing without blacklist")
        except OSError as e:
            logger.warning(f"Blacklist file read error: {e} — continuing without blacklist")

    def is_blacklisted(self, artist: str, title: str) -> bool:
        """
        Check if an artist+title combination is blacklisted.

        Args:
            artist: Artist name from recognition provider
            title: Song title from recognition provider

        Returns:
            True if the match should be rejected, False if it should be accepted
        """
        if not self._active:
            return False

        artist_norm = artist.lower().strip()
        title_norm = title.lower().strip()

        # 1. Check artists list
        for pattern in self._artists:
            if fnmatch.fnmatch(artist_norm, pattern.lower().strip()):
                logger.debug(f"Blacklist: artist match '{artist}' matched pattern '{pattern}'")
                return True

        # 2. Check titles list
        for pattern in self._titles:
            if fnmatch.fnmatch(title_norm, pattern.lower().strip()):
                logger.debug(f"Blacklist: title match '{title}' matched pattern '{pattern}'")
                return True

        # 3. Check combos list
        for combo in self._combos:
            a_pattern = combo.get("artist", "*").lower().strip() or "*"
            t_pattern = combo.get("title", "*").lower().strip() or "*"
            if fnmatch.fnmatch(artist_norm, a_pattern) and fnmatch.fnmatch(title_norm, t_pattern):
                logger.debug(f"Blacklist: combo match '{artist} - {title}' matched '{combo}'")
                return True

        return False

    def _create_template(self, path: Path) -> None:
        """Write an empty template blacklist.json for the user to edit."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_TEMPLATE_CONTENT, f, indent=2)
            logger.info(f"Created blacklist template at {path}")
        except OSError as e:
            logger.warning(f"Could not create blacklist template: {e}")

    def __repr__(self) -> str:
        return (
            f"RecognitionBlacklist(artists={len(self._artists)}, "
            f"titles={len(self._titles)}, combos={len(self._combos)}, "
            f"active={self._active})"
        )
