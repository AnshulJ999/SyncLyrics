"""
Daily check-in with the SyncLyrics server: update check + optional anonymous usage stats.

One request a day at most, only when at least one of the two settings is on.
Nothing about what is playing is ever sent. What is sent is documented in
docs/Usage Stats.md - keep the two in step.
"""
import asyncio
import platform
import time
from typing import Optional

import requests

import app_info
import state_manager
from config import conf, _safe_bool
from logging_config import get_logger
from version import VERSION

logger = get_logger(__name__)

CHECKIN_URL = "https://sl.anshuljain.net/v1/checkin"
CHECK_INTERVAL = 24 * 3600
FIRST_CHECK_DELAY = 60      # let startup finish first
LOOP_SLEEP = 3600           # re-evaluate hourly (settings can change at runtime)
RETRY_AFTER_FAILURE = 6 * 3600
_TIMEOUT = (5, 10)


def updates_enabled() -> bool:
    return _safe_bool(conf("updates.check_enabled"), True)


def stats_enabled() -> bool:
    return _safe_bool(conf("updates.usage_stats"), True)


def _enabled_names(prefix: str) -> list:
    from settings import settings
    names = []
    for key in settings._definitions:
        if key.startswith(prefix) and key.endswith(".enabled") and _safe_bool(conf(key), False):
            names.append(key[len(prefix):-len(".enabled")])
    return sorted(names)


# --- Playback since the last check-in (usage stats only) ---
# Only names leave the app (which sources played, which providers served lyrics,
# the most used of each) - never counts or song details.
_MAX_TRACKS = 500
_usage: dict = {"played": False, "tracks": {}}  # track_id -> [source, provider]


def note_playback(metadata: dict, provider: Optional[str]) -> None:
    """Called on each /current-track poll."""
    if not metadata or not metadata.get("is_playing") or not stats_enabled():
        return
    track_id = metadata.get("track_id") or f"{metadata.get('artist')}_{metadata.get('title')}"
    tracks = _usage["tracks"]
    if track_id not in tracks:
        if len(tracks) >= _MAX_TRACKS:
            return
        tracks[track_id] = ["", ""]
    _usage["played"] = True
    entry = tracks[track_id]
    entry[0] = metadata.get("source") or entry[0]
    entry[1] = provider or entry[1]


def _usage_summary() -> dict:
    from collections import Counter
    sources = Counter(s for s, _ in _usage["tracks"].values() if s)
    providers = Counter(p for _, p in _usage["tracks"].values() if p)
    return {
        "played": _usage["played"],
        "sources_used": sorted(sources),
        "providers_used": sorted(providers),
        "top_source": sources.most_common(1)[0][0] if sources else None,
        "top_provider": providers.most_common(1)[0][0] if providers else None,
    }


def _reset_usage() -> None:
    _usage["played"] = False
    _usage["tracks"] = {}


def build_payload(include_stats: bool) -> dict:
    payload = {
        "schema": 1,
        "version": VERSION,
        "install_type": app_info.get_install_type(),
        "os": app_info.get_os_name().lower(),
    }
    if include_stats:
        from system_utils.sources import get_all_sources_sorted
        try:
            sources = [s["name"] for s in get_all_sources_sorted()]
        except Exception:
            sources = []
        payload.update(
            install_id=state_manager.get_install_id(),
            arch=platform.machine().lower(),
            python=platform.python_version(),
            sources=sources,
            providers=_enabled_names("providers."),
            **_usage_summary(),
        )
    return payload


def _post(payload: dict) -> Optional[dict]:
    resp = requests.post(CHECKIN_URL, json=payload, timeout=_TIMEOUT,
                         headers={"User-Agent": f"SyncLyrics/{VERSION}"})
    if resp.status_code != 200:
        logger.debug(f"Check-in returned HTTP {resp.status_code}")
        return None
    data = resp.json()
    return data if isinstance(data, dict) else None


async def check_now() -> bool:
    """Run one check-in if either setting is on. Returns True on success."""
    want_updates, want_stats = updates_enabled(), stats_enabled()
    if not (want_updates or want_stats):
        return False
    try:
        data = await asyncio.to_thread(_post, build_payload(want_stats))
    except Exception as e:
        logger.debug(f"Check-in failed: {e}")
        data = None

    now = time.time()
    if data is None:
        state_manager.update_state_values({"checkin_retry_at": now + RETRY_AFTER_FAILURE})
        return False

    latest = data.get("latest")
    update_info = {
        "latest": latest if isinstance(latest, str) else None,
        "url": data.get("url") if isinstance(data.get("url"), str) else None,
        "notice": data.get("notice") if isinstance(data.get("notice"), str) else None,
        "checked_at": now,
    }
    state_manager.update_state_values({"last_checkin_at": now, "checkin_retry_at": 0, "update_info": update_info})
    if want_stats:
        _reset_usage()
    return True


def _due() -> bool:
    now = time.time()
    last = state_manager.get_state_value("last_checkin_at") or 0
    retry_at = state_manager.get_state_value("checkin_retry_at") or 0
    return now - last >= CHECK_INTERVAL and now >= retry_at


async def checkin_loop() -> None:
    await asyncio.sleep(FIRST_CHECK_DELAY)
    while True:
        try:
            if _due():
                await check_now()
        except Exception as e:
            logger.debug(f"Check-in loop error: {e}")
        await asyncio.sleep(LOOP_SLEEP)


def get_update_status() -> dict:
    """What the UI needs: is an update available, and where to get it."""
    enabled = updates_enabled()
    info = state_manager.get_state_value("update_info") or {}
    latest = info.get("latest") if isinstance(info, dict) else None
    available = bool(enabled and latest and app_info.version_tuple(latest) > app_info.version_tuple(VERSION))
    return {
        "checks_enabled": enabled,
        "stats_enabled": stats_enabled(),
        "available": available,
        "latest": latest if available else None,
        "url": (info.get("url") or f"{app_info.REPO_URL}/releases/latest") if available else None,
        "notice": info.get("notice") if enabled and isinstance(info, dict) else None,
        "checked_at": info.get("checked_at") if isinstance(info, dict) else None,
    }
