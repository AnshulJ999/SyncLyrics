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


def _blocks(lines: list) -> list:
    """Group body lines into ("p", text) and ("ul", [items]) blocks."""
    blocks, bullets, para = [], [], []

    def flush():
        if para:
            blocks.append(("p", " ".join(para)))
            para.clear()
        if bullets:
            blocks.append(("ul", list(bullets)))
            bullets.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
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
    return blocks


def _render_blocks(blocks: list) -> str:
    return "".join(
        f"<p>{_inline(body)}</p>" if kind == "p"
        else "<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in body) + "</ul>"
        for kind, body in blocks
    )


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[*`])")


def _render_feature(title: str, lines: list) -> str:
    """A #### entry: title + first sentence, the rest behind a native disclosure."""
    blocks = _blocks(lines)
    summary, rest = "", blocks
    if blocks and blocks[0][0] == "p":
        summary, remainder = _split_first_sentence(blocks[0][1])
        rest = ([("p", remainder)] if remainder else []) + blocks[1:]
    head = f'<span class="cl-title">{_inline(title)}</span>'
    if summary:
        head += f'<span class="cl-sum">{_inline(summary)}</span>'
    if not rest:
        return f'<div class="cl-item">{head}</div>'
    return (f'<details class="cl-item"><summary>{head}'
            f'<i class="bi bi-chevron-down cl-chevron" aria-hidden="true"></i></summary>'
            f'<div class="cl-more">{_render_blocks(rest)}</div></details>')


def _split_first_sentence(text: str) -> tuple:
    parts = _SENTENCE_END.split(text, maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


_SECTION_ICONS = [
    ("important", "bi-exclamation-triangle"), ("breaking", "bi-exclamation-triangle"),
    ("feature", "bi-stars"), ("fix", "bi-wrench"), ("home assistant", "bi-house-door"),
    ("document", "bi-journal-text"), ("technical", "bi-tools"),
]


def _render_section(lines: list) -> str:
    """One version's body: ### sections, each with optional #### feature entries."""
    sections, current = [], None
    for line in lines:
        if line.startswith("### "):
            current = {"title": _heading_text(line[4:]), "intro": [], "features": []}
            sections.append(current)
        elif line.startswith("#### ") and current is not None:
            current["features"].append({"title": _heading_text(line[5:]), "lines": []})
        elif current is None:
            continue
        elif current["features"]:
            current["features"][-1]["lines"].append(line)
        else:
            current["intro"].append(line)

    out = []
    for sec in sections:
        key = sec["title"].lower()
        icon = next((i for word, i in _SECTION_ICONS if word in key), "bi-dot")
        callout = "important" in key or "breaking" in key
        body = _render_blocks(_blocks(sec["intro"]))
        if callout:
            body = f'<div class="cl-callout">{body}</div>'
        body += "".join(_render_feature(f["title"], f["lines"]) for f in sec["features"])
        out.append(f'<section class="cl-section"><h5 class="cl-label">'
                   f'<i class="bi {icon}" aria-hidden="true"></i>{html.escape(sec["title"])}</h5>{body}</section>')
    return "".join(out)


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
