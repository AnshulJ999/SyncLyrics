"""
App identity for the UI: version, install type, changelog, first-run/update panels
and donation links. The daily check-in lives in checkin.py.
"""
import html
import os
import platform
import re
import sys
import time
from typing import Optional

import state_manager
from config import ROOT_DIR
from logging_config import get_logger
from version import VERSION

logger = get_logger(__name__)

# --- Code-level switches ---

# "install": What's New shows once per install.
# "browser": once per browser/screen, for browsers opened within BROWSER_SCOPE_DAYS of the update.
WHATS_NEW_SCOPE = "install"
BROWSER_SCOPE_DAYS = 14

# Set "enabled": False to hide a platform everywhere (panels, Overview page).
DONATION_LINKS = [
    {"id": "github", "label": "GitHub Sponsors", "url": "https://github.com/sponsors/AnshulJ999", "icon": "bi-github", "enabled": True},
    {"id": "kofi", "label": "Ko-fi", "url": "https://ko-fi.com/anshul99", "icon": "bi-cup-hot", "enabled": True},
    {"id": "patreon", "label": "Patreon", "url": "https://www.patreon.com/AnshulJain", "icon": "bi-heart", "enabled": True},
    {"id": "paypal", "label": "PayPal", "url": "https://paypal.me/AnshulJain99", "icon": "bi-paypal", "enabled": True},
]

REPO_URL = "https://github.com/AnshulJ999/SyncLyrics"
CHANGELOG_FILE = ROOT_DIR / "CHANGELOG.md"


def get_install_type() -> str:
    """One of: ha_addon, docker, appimage, exe, source."""
    if os.path.exists("/data/options.json") or os.getenv("SUPERVISOR_TOKEN"):
        return "ha_addon"
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return "docker"
    if os.getenv("APPIMAGE"):
        return "appimage"
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return "exe"
    return "source"


def get_os_name() -> str:
    return {"Darwin": "macOS"}.get(platform.system(), platform.system())


def version_tuple(version: str) -> tuple:
    """'v2.5.0-beta' -> (2, 5, 0). Unparseable parts count as 0."""
    core = str(version).lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for piece in core.split(".")[:3]:
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


# --- Changelog -> HTML (small, safe subset of Markdown) ---

_changelog_cache: dict = {"mtime": None, "html": {}}

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>', out)
    return out


def _heading_text(text: str) -> str:
    # Drop leading emoji/symbols ("✨ New Features" -> "New Features")
    return re.sub(r"^[^\w(]+", "", text).strip()


def _render_section(lines: list) -> str:
    parts, bullets, para = [], [], []

    def flush():
        if para:
            parts.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{_inline(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#### "):
            flush()
            parts.append(f"<h6>{_inline(_heading_text(line[5:]))}</h6>")
        elif line.startswith("### "):
            flush()
            parts.append(f"<h5>{_inline(_heading_text(line[4:]))}</h5>")
        elif line.startswith(("- ", "* ")):
            if para:
                flush()
            bullets.append(line[2:])
        elif bullets and raw.startswith("  "):
            bullets[-1] += " " + line  # wrapped bullet
        else:
            if bullets:
                flush()
            para.append(line)
    flush()
    return "".join(parts)


def get_changelog_html(version: str = VERSION) -> Optional[str]:
    """HTML for one version's CHANGELOG section, or None if it isn't there."""
    try:
        mtime = CHANGELOG_FILE.stat().st_mtime
    except OSError:
        return None
    if _changelog_cache["mtime"] != mtime:
        _changelog_cache["mtime"] = mtime
        _changelog_cache["html"] = {}
    if version in _changelog_cache["html"]:
        return _changelog_cache["html"][version]

    target = version.lstrip("vV").split("-")[0]
    section, inside = [], False
    try:
        for line in CHANGELOG_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                if inside:
                    break
                inside = bool(re.match(rf"^##\s*\[?v?{re.escape(target)}\]?(\s|$)", line))
                continue
            if inside:
                section.append(line)
    except OSError as e:
        logger.warning(f"Could not read changelog: {e}")
        return None

    result = _render_section(section) if section else None
    _changelog_cache["html"][version] = result
    return result


# --- Welcome / What's New state ---

def init_install_markers() -> None:
    """Call once at startup, before the UI is served."""
    values = {}
    if not state_manager.STATE_EXISTED_AT_STARTUP:
        values.update(welcome_pending=True, last_seen_version=VERSION)
    if state_manager.get_state_value("current_version") != VERSION:
        values.update(current_version=VERSION, current_version_since=time.time())
    if values:
        state_manager.update_state_values(values)


def get_panel_state() -> dict:
    since = state_manager.get_state_value("current_version_since") or 0
    return {
        "welcome": bool(state_manager.get_state_value("welcome_pending")),
        "whats_new_scope": WHATS_NEW_SCOPE,
        "whats_new_unseen": state_manager.get_state_value("last_seen_version") != VERSION,
        "updated_recently": (time.time() - since) < BROWSER_SCOPE_DAYS * 86400,
    }


def mark_panel_seen(panel: str) -> None:
    if panel == "welcome":
        state_manager.update_state_values({"welcome_pending": False, "last_seen_version": VERSION})
    elif panel == "whats_new":
        state_manager.set_last_seen_version(VERSION)


def get_donation_links() -> list:
    return [{k: v for k, v in link.items() if k != "enabled"} for link in DONATION_LINKS if link.get("enabled")]
